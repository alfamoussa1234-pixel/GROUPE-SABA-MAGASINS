import os, secrets
from decimal import Decimal, InvalidOperation
from flask import Flask, request, session, redirect, url_for, abort, render_template_string, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import select
from werkzeug.security import generate_password_hash, check_password_hash
app=Flask(__name__)
app.secret_key=os.environ.get('SECRET_KEY') or secrets.token_hex(32)
uri=os.environ.get('DATABASE_URL','sqlite:///magasin.db').replace('postgres://','postgresql+psycopg://',1).replace('postgresql://','postgresql+psycopg://',1)
app.config.update(SQLALCHEMY_DATABASE_URI=uri,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=bool(os.environ.get('RENDER')))
db=SQLAlchemy(app)
class User(db.Model):
 id=db.Column(db.Integer,primary_key=True); name=db.Column(db.String(100),unique=True,nullable=False); password=db.Column(db.String(256),nullable=False); role=db.Column(db.String(20),nullable=False); store=db.Column(db.Integer,nullable=False,default=0)
class Item(db.Model):
 id=db.Column(db.Integer,primary_key=True); store=db.Column(db.Integer,nullable=False); name=db.Column(db.String(200),nullable=False); category=db.Column(db.String(100)); unit=db.Column(db.String(30)); qty=db.Column(db.Numeric(18,3),default=0,nullable=False); minimum=db.Column(db.Numeric(18,3),default=0); price=db.Column(db.Numeric(18,2),default=0)
class Move(db.Model):
 id=db.Column(db.Integer,primary_key=True); item_id=db.Column(db.Integer,db.ForeignKey('item.id'),nullable=False); kind=db.Column(db.String(20)); qty=db.Column(db.Numeric(18,3)); destination=db.Column(db.String(250)); status=db.Column(db.String(20),default='attente'); author=db.Column(db.String(100)); validator=db.Column(db.String(100)); reason=db.Column(db.String(250)); created=db.Column(db.DateTime,server_default=db.func.now()); decided=db.Column(db.DateTime); item=db.relationship(Item)
from datetime import datetime, timezone
with app.app_context():
 db.create_all()
 if not User.query.first() and os.environ.get('ADMIN_PASSWORD'):
  db.session.add(User(name='admin',password=generate_password_hash(os.environ['ADMIN_PASSWORD']),role='admin',store=0)); db.session.commit()
def user(): return db.session.get(User,session.get('uid')) if session.get('uid') else None
def allowed(u,s): return u.store==0 or u.store==s
def num(key,positive=False):
 try:
  n=Decimal(request.form[key]); assert n.is_finite() and n>=0 and (not positive or n>0) and n<10**12; return n
 except (ValueError,InvalidOperation,AssertionError,KeyError): abort(400,'Quantité ou prix invalide')
@app.before_request
def csrf():
 if 'csrf' not in session: session['csrf']=secrets.token_hex(32)
 if request.method=='POST' and not secrets.compare_digest(session['csrf'],request.form.get('csrf','')): abort(400)
@app.route('/login',methods=['GET','POST'])
def login():
 if request.method=='POST':
  u=User.query.filter_by(name=request.form.get('name')).first()
  if u and check_password_hash(u.password,request.form.get('password','')): session.clear();session['uid']=u.id;return redirect('/')
  flash('Identifiants incorrects')
 return page('Connexion','<form method="post">'+token()+'<input name="name" placeholder="Identifiant" required><input name="password" type="password" placeholder="Mot de passe" required><button>Connexion</button></form>')
def token(): return '<input type="hidden" name="csrf" value="{{ session.csrf }}">'
@app.post('/logout')
def logout(): session.clear();return redirect('/login')
@app.route('/',methods=['GET','POST'])
def index():
 u=user()
 if not u:return redirect('/login')
 if request.method=='POST':
  action=request.form.get('action')
  if action=='item':
   s=int(request.form['store']);
   if s not in (1,2) or not allowed(u,s) or u.role not in ('admin','magasinier'):abort(403)
   db.session.add(Item(store=s,name=request.form['name'],category=request.form['category'],unit=request.form['unit'],minimum=num('minimum'),price=num('price')))
  elif action=='move':
   i=db.session.get(Item,int(request.form['item'])); k=request.form['kind']
   if not i or not allowed(u,i.store) or u.role not in ('admin','magasinier') or k not in ('entrée','sortie','retour','rebut'):abort(403)
   db.session.add(Move(item_id=i.id,kind=k,qty=num('qty',True),destination=request.form['destination'],author=u.name))
  elif action=='decide':
   if u.role not in ('admin','validateur'):abort(403)
   m=db.session.execute(select(Move).where(Move.id==int(request.form['move'])).with_for_update()).scalar_one_or_none()
   if not m or not allowed(u,m.item.store) or m.author==u.name:abort(403)
   if m.status!='attente':abort(409,'Déjà traité')
   decision=request.form['decision']; reason=request.form.get('reason','').strip()
   if decision=='valide':
    i=db.session.execute(select(Item).where(Item.id==m.item_id).with_for_update()).scalar_one(); delta=m.qty if m.kind in ('entrée','retour') else -m.qty
    if i.qty+delta<0:abort(409,'Stock insuffisant')
    i.qty+=delta
   elif decision!='rejete' or not reason:abort(400,'Motif requis')
   m.status=decision;m.validator=u.name;m.reason=reason;m.decided=datetime.now(timezone.utc)
  elif action=='user':
   if u.role!='admin':abort(403)
   role=request.form['role']; s=int(request.form['store']);pw=request.form['password']
   if role not in ('admin','magasinier','validateur','lecture') or s not in (0,1,2) or len(pw)<12 or (role=='magasinier' and s==0):abort(400)
   existing=User.query.filter_by(name=request.form['name']).first()
   if existing:abort(409,'Identifiant existant')
   db.session.add(User(name=request.form['name'],password=generate_password_hash(pw),role=role,store=s))
  else:abort(400)
  db.session.commit();return redirect('/')
 items=Item.query.filter(Item.store.in_([1,2] if u.store==0 else [u.store])).order_by(Item.store,Item.name).all()
 moves=Move.query.filter(Move.item_id.in_([i.id for i in items])).order_by(Move.id.desc()).all()
 return page('Gestion des magasins',BODY,u=u,items=items,moves=moves,stores=[s for s in (1,2) if allowed(u,s)],names={1:'Gardiennage',2:'Nettoyage'})
STYLE='''<style>*{box-sizing:border-box}body{font:15px system-ui;background:#eef3f8;color:#12324e;margin:0}header{background:#0c3458;color:white;padding:16px 5%;display:flex;align-items:center;gap:24px;flex-wrap:wrap}header img{width:180px;background:white;border-radius:8px;padding:7px}header strong{font-size:21px}main{max-width:1240px;margin:auto;padding:24px}section{background:white;padding:22px;margin-bottom:20px;border-radius:16px;overflow:auto;box-shadow:0 4px 20px #12324e0b;border:1px solid #dce5ef}input,select,button{padding:12px;margin:4px;border:1px solid #cbd5e1;border-radius:9px;font:inherit;max-width:100%}button{background:#176a62;color:white;border:0;cursor:pointer;font-weight:600}button:hover{background:#104e49}button[value=rejete]{background:#b83242}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:12px;border-bottom:1px solid #e4eaf0}th{background:#eef4fa;color:#365671}.alert{color:#a62424;font-weight:bold}h2{font-size:20px;margin-top:0}form{margin:8px 0}small{color:#64748b}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:15px 0}.metric{background:#eef5fb;border-radius:12px;padding:16px}.metric b{display:block;font-size:24px;color:#0c3458}.metric span{font-size:13px;color:#45627a}@media(max-width:600px){main{padding:12px}header{padding:12px;gap:10px}header img{width:125px}header strong{font-size:16px}section{padding:14px}input,select{width:100%;margin:4px 0}td,th{min-width:105px}}</style>'''
def page(title,body,**ctx):return render_template_string('<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+'</title>'+STYLE+'<header><img src="/static/logo.png" alt="GROUPE SABA"><strong>GROUPE SABA · Gestion des magasins</strong> · '+title+'</header><main>{% for m in get_flashed_messages() %}<p>{{m}}</p>{% endfor %}'+body+'</main></html>',**ctx)
BODY='''<section><b>{{u.name}} · {{u.role}}</b><form action="/logout" method="post">TOKEN<button>Déconnexion</button></form></section>{% for s in stores %}<section><h2>Magasin {{names[s]}}</h2>{% set articles=items|selectattr('store','equalto',s)|list %}{% set ns=namespace(value=0,alerts=0,pending=0) %}{% for i in articles %}{% set ns.value=ns.value+i.qty*i.price %}{% if i.qty<=i.minimum %}{% set ns.alerts=ns.alerts+1 %}{% endif %}{% endfor %}{% for m in moves if m.item.store==s and m.status=='attente' %}{% set ns.pending=ns.pending+1 %}{% endfor %}<div class="metrics"><div class="metric"><span>Références</span><b>{{articles|length}}</b></div><div class="metric"><span>Valeur du stock (FCFA)</span><b>{{'{:,.0f}'.format(ns.value).replace(',', ' ')}}</b></div><div class="metric"><span>Articles en alerte</span><b>{{ns.alerts}}</b></div><div class="metric"><span>Validations en attente</span><b>{{ns.pending}}</b></div></div><table><tr><th>Article</th><th>Catégorie</th><th>Stock</th><th>Prix FCFA</th></tr>{% for i in items if i.store==s %}<tr><td>{{i.name}}</td><td>{{i.category}}</td><td class="{{'alert' if i.qty<=i.minimum else ''}}">{{i.qty}} {{i.unit}}</td><td>{{i.price}}</td></tr>{% endfor %}</table></section>{% endfor %}{% if u.role in ['admin','magasinier'] %}<section><h2>Nouvel article</h2><form method="post">TOKEN<input type="hidden" name="action" value="item"><select name="store">{% for s in stores %}<option value="{{s}}">{{names[s]}}</option>{% endfor %}</select><input name="name" placeholder="Désignation" required><input name="category" placeholder="Catégorie"><input name="unit" placeholder="Unité" required><input name="minimum" type="number" min="0" step="0.001" placeholder="Seuil minimum" required><input name="price" type="number" min="0" step="0.01" placeholder="Prix FCFA" required><button>Créer</button></form></section><section><h2>Soumettre un mouvement</h2><form method="post">TOKEN<input type="hidden" name="action" value="move"><select name="item">{% for i in items %}<option value="{{i.id}}">{{names[i.store]}} — {{i.name}}</option>{% endfor %}</select><select name="kind">{% for k in ['entrée','sortie','retour','rebut'] %}<option>{{k}}</option>{% endfor %}</select><input name="qty" type="number" min="0.001" step="0.001" required placeholder="Quantité"><input name="destination" placeholder="Site / agent / fournisseur" required><button>Soumettre</button></form></section>{% endif %}<section><h2>Mouvements et validations</h2><table><tr><th>N°</th><th>Magasin / article</th><th>Opération</th><th>Destination</th><th>Statut</th><th>Traçabilité</th></tr>{% for m in moves %}<tr><td>{{m.id}}</td><td>{{names[m.item.store]}} / {{m.item.name}}</td><td>{{m.kind}} {{m.qty}}</td><td>{{m.destination}}</td><td>{{m.status}}{% if m.status=='attente' and u.role in ['admin','validateur'] and m.author!=u.name %}<form method="post">TOKEN<input type="hidden" name="action" value="decide"><input type="hidden" name="move" value="{{m.id}}"><input name="reason" placeholder="Motif si rejet"><button name="decision" value="valide">Valider</button><button name="decision" value="rejete">Rejeter</button></form>{% endif %}</td><td>{{m.author}} · {{m.created}}<br>{{m.validator or ''}} {{m.decided or ''}} {{m.reason or ''}}</td></tr>{% endfor %}</table></section>{% if u.role=='admin' %}<section><h2>Créer un utilisateur</h2><form method="post">TOKEN<input type="hidden" name="action" value="user"><input name="name" required placeholder="Identifiant"><input name="password" type="password" minlength="12" required placeholder="Mot de passe"><select name="role"><option>magasinier</option><option>validateur</option><option>lecture</option><option>admin</option></select><select name="store"><option value="0">Les deux</option><option value="1">Gardiennage</option><option value="2">Nettoyage</option></select><button>Créer</button></form></section>{% endif %}'''.replace('TOKEN',token())
if __name__=='__main__': app.run()
