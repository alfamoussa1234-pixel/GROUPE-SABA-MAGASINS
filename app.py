import os, secrets, io
from openpyxl import load_workbook
from decimal import Decimal, InvalidOperation
from flask import Flask, request, session, redirect, url_for, abort, render_template_string, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import select
from flask import send_file
from openpyxl import Workbook
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
class Site(db.Model):
 id=db.Column(db.Integer,primary_key=True); store=db.Column(db.Integer,nullable=False); name=db.Column(db.String(200),nullable=False); address=db.Column(db.String(250)); __table_args__=(db.UniqueConstraint('store','name'),)
class ArchivedItem(db.Model):
 item_id=db.Column(db.Integer,db.ForeignKey('item.id'),primary_key=True)
class ArchivedSite(db.Model):
 site_id=db.Column(db.Integer,db.ForeignKey('site.id'),primary_key=True)
class DisabledUser(db.Model):
 user_id=db.Column(db.Integer,db.ForeignKey('user.id'),primary_key=True)
from datetime import datetime, timezone
with app.app_context():
 db.create_all()
 if not User.query.first() and os.environ.get('ADMIN_PASSWORD'):
  db.session.add(User(name='admin',password=generate_password_hash(os.environ['ADMIN_PASSWORD']),role='admin',store=0)); db.session.commit()
def user():
 u=db.session.get(User,session.get('uid')) if session.get('uid') else None
 return u if u and not db.session.get(DisabledUser,u.id) else None
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
  if u and not db.session.get(DisabledUser,u.id) and check_password_hash(u.password,request.form.get('password','')): session.clear();session['uid']=u.id;return redirect('/')
  flash('Identifiants incorrects')
 return page('Connexion','<form method="post">'+token()+'<input name="name" placeholder="Identifiant" required><input name="password" type="password" placeholder="Mot de passe" required><button>Connexion</button></form>')
def token(): return '<input type="hidden" name="csrf" value="{{ session.csrf }}">'
@app.post('/logout')
def logout(): session.clear();return redirect('/login')
@app.route('/',methods=['GET','POST'])
@app.route('/module/<active>',methods=['GET','POST'])
def index(active='dashboard'):
 u=user()
 if not u:return redirect('/login')
 if active not in PAGES:abort(404)
 if active=='comptes' and u.role!='admin':abort(403)
 if request.method=='POST':
  action=request.form.get('action')
  if action in ('edit_item','archive_item','restore_item','delete_item'):
   if u.role!='admin':abort(403)
   i=db.session.execute(select(Item).where(Item.id==int(request.form['id'])).with_for_update()).scalar_one_or_none()
   if not i or not allowed(u,i.store):abort(404)
   archived=db.session.get(ArchivedItem,i.id)
   if action=='edit_item':
    name=request.form['name'].strip(); unit=request.form['unit'].strip(); cat=request.form.get('category','').strip()
    if not name or not unit or len(name)>200 or len(unit)>30 or len(cat)>100:abort(400)
    if Move.query.filter_by(item_id=i.id).first() and unit!=i.unit:abort(409,'Unité non modifiable après mouvement')
    i.name=name;i.unit=unit;i.category=cat;i.minimum=num('minimum');i.price=num('price')
   elif action=='restore_item':
    if archived:db.session.delete(archived)
   elif action=='archive_item':
    if i.qty!=0 or Move.query.filter_by(item_id=i.id,status='attente').first():abort(409,'Stock non nul ou mouvement en attente')
    if not archived:db.session.add(ArchivedItem(item_id=i.id))
   else:
    if i.qty!=0 or Move.query.filter_by(item_id=i.id).first():abort(409,'Historique existant : archivez plutôt cet article')
    if archived:db.session.delete(archived);db.session.flush()
    db.session.delete(i)
   flash('Article mis à jour')
  elif action in ('edit_site','archive_site','restore_site'):
   if u.role!='admin':abort(403)
   site=db.session.get(Site,int(request.form['id']))
   if not site or not allowed(u,site.store):abort(404)
   a=db.session.get(ArchivedSite,site.id)
   if action=='edit_site':
    name=request.form['name'].strip()
    if not name or len(name)>200:abort(400)
    if Site.query.filter(Site.store==site.store,Site.name==name,Site.id!=site.id).first():abort(409,'Site existant')
    site.name=name;site.address=request.form.get('address','')[:250]
   elif action=='archive_site' and not a:db.session.add(ArchivedSite(site_id=site.id))
   elif action=='restore_site' and a:db.session.delete(a)
   flash('Site mis à jour')
  elif action in ('reset_user','disable_user','enable_user'):
   if u.role!='admin':abort(403)
   target=db.session.get(User,int(request.form['id']))
   if not target or target.id==u.id or not allowed(u,target.store):abort(403)
   a=db.session.get(DisabledUser,target.id)
   if action=='reset_user':
    pw=request.form['password']
    if len(pw)<12:abort(400)
    target.password=generate_password_hash(pw)
   elif action=='disable_user' and not a:db.session.add(DisabledUser(user_id=target.id))
   elif action=='enable_user' and a:db.session.delete(a)
   flash('Compte mis à jour')
  elif action=='site':
   if u.role!='admin':abort(403)
   st=int(request.form['store']); name=request.form['name'].strip()
   if st not in (1,2) or not allowed(u,st) or not name or len(name)>200:abort(400)
   if Site.query.filter_by(store=st,name=name).first():abort(409,'Site existant')
   db.session.add(Site(store=st,name=name,address=request.form.get('address','')[:250]))
  elif action=='import':
   if u.role not in ('admin','magasinier'):abort(403)
   st=int(request.form['store'])
   if st not in (1,2) or not allowed(u,st):abort(403)
   f=request.files.get('file')
   if not f:abort(400,'Fichier requis')
   raw=f.read(2000001)
   if len(raw)>2000000:abort(400,'Fichier trop volumineux')
   try:
    import zipfile
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
     if sum(x.file_size for x in z.infolist())>20000000:raise ValueError()
    wb=load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
    ws=wb['Articles']; rows=ws.iter_rows(values_only=True)
    if tuple(next(rows))!=('Désignation','Catégorie','Unité','Seuil minimum','Prix FCFA'):raise ValueError()
    seen={i.name.strip().casefold() for i in Item.query.filter_by(store=st).all()}; count=0
    for idx,row in enumerate(rows):
     if idx>=5000:raise ValueError()
     if not any(v is not None for v in row):continue
     name,cat,unit,minimum,price=row
     name=str(name or '').strip();cat=str(cat or '').strip();unit=str(unit or '').strip()
     minimum=Decimal(str(minimum or 0));price=Decimal(str(price or 0))
     if not name or not unit or len(name)>200 or len(cat)>100 or len(unit)>30 or not all(x.is_finite() and 0<=x<10**12 for x in (minimum,price)):raise ValueError()
     if name.casefold() in seen:continue
     seen.add(name.casefold());db.session.add(Item(store=st,name=name,category=cat,unit=unit,minimum=minimum,price=price));count+=1
    wb.close()
   except Exception:
    db.session.rollback();abort(400,'Fichier Excel invalide : vérifiez les cinq colonnes du modèle')
   flash(f'{count} articles importés. Stock initial à zéro; doublons ignorés.')
  elif action=='item':
   s=int(request.form['store']);
   if s not in (1,2) or not allowed(u,s) or u.role not in ('admin','magasinier'):abort(403)
   db.session.add(Item(store=s,name=request.form['name'],category=request.form['category'],unit=request.form['unit'],minimum=num('minimum'),price=num('price')))
  elif action=='move':
   i=db.session.get(Item,int(request.form['item'])); k=request.form['kind']
   if not i or db.session.get(ArchivedItem,i.id) or not allowed(u,i.store) or u.role not in ('admin','magasinier') or k not in ('entrée','sortie','retour','rebut'):abort(403)
   dest=request.form.get('destination','').strip(); siteid=request.form.get('site')
   if siteid:
    site=db.session.get(Site,int(siteid))
    if not site or db.session.get(ArchivedSite,site.id) or site.store!=i.store:abort(400,'Site incompatible avec le magasin')
    dest=site.name+(' — '+dest if dest else '')
   if not dest or len(dest)>250:abort(400,'Destination requise')
   db.session.add(Move(item_id=i.id,kind=k,qty=num('qty',True),destination=dest,author=u.name))
  elif action=='decide':
   if u.role not in ('admin','validateur'):abort(403)
   m=db.session.execute(select(Move).where(Move.id==int(request.form['move'])).with_for_update()).scalar_one_or_none()
   if not m or not allowed(u,m.item.store) or m.author==u.name:abort(403)
   if m.status!='attente':abort(409,'Déjà traité')
   decision=request.form['decision']; reason=request.form.get('reason','').strip()
   if decision=='valide':
    i=db.session.execute(select(Item).where(Item.id==m.item_id).with_for_update()).scalar_one(); delta=m.qty if m.kind in ('entrée','retour') else -m.qty
    if db.session.get(ArchivedItem,i.id):abort(409,'Article archivé')
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
  db.session.commit();return redirect(url_for('index',active=active))
 items=Item.query.filter(Item.store.in_([1,2] if u.store==0 else [u.store])).order_by(Item.store,Item.name).all()
 moves=Move.query.filter(Move.item_id.in_([i.id for i in items])).order_by(Move.id.desc()).all()
 sites=Site.query.filter(Site.store.in_([1,2] if u.store==0 else [u.store])).order_by(Site.name).all()
 archived={a.item_id for a in ArchivedItem.query.all()}; archived_sites={a.site_id for a in ArchivedSite.query.all()}; users=User.query.all() if u.role=='admin' else []; disabled={a.user_id for a in DisabledUser.query.all()} if users else set()
 return page(TITLES[active],NAV+PAGES[active],active=active,kind=KINDS.get(active),u=u,items=items,moves=moves,sites=sites,archived=archived,archived_sites=archived_sites,users=users,disabled=disabled,stores=[s for s in (1,2) if allowed(u,s)],names={1:'Gardiennage',2:'Nettoyage'})
@app.get('/export')
def export():
 u=user()
 if not u:abort(403)
 st=request.args.get('store',type=int)
 if st not in (1,2) or not allowed(u,st):abort(403)
 wb=Workbook(); ws=wb.active;ws.title='Stock';ws.append(['Désignation','Catégorie','Unité','Stock','Seuil minimum','Prix FCFA','Valeur FCFA','Archivé'])
 def safe(v):return "'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v
 for i in Item.query.filter_by(store=st).order_by(Item.name):ws.append([safe(i.name),safe(i.category),safe(i.unit),float(i.qty),float(i.minimum or 0),float(i.price or 0),float(i.qty*(i.price or 0)),bool(db.session.get(ArchivedItem,i.id))])
 ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
 out=io.BytesIO();wb.save(out);out.seek(0);return send_file(out,as_attachment=True,download_name='stock-'+str(st)+'.xlsx')

STYLE='<style>*{box-sizing:border-box}body{margin:0;background:#f0f4f9;color:#14324e;font:15px system-ui}header{background:white;border-bottom:1px solid #dce6ef;display:flex;align-items:center;gap:24px;padding:16px 24px}header img{width:180px;max-width:40%;height:auto}header strong{font-size:19px}main{padding:28px;max-width:1600px;margin:auto}body:has(nav) main{margin-left:250px}nav{position:fixed;top:0;bottom:0;left:0;width:250px;background:#103451;padding:24px 16px;overflow:auto;color:white;display:flex;flex-direction:column;gap:5px}body:has(nav) header{margin-left:250px}nav img{background:white;border-radius:9px;width:100%;padding:8px;margin-bottom:18px}nav a{color:#dce9f7;text-decoration:none;padding:12px 15px;border-radius:8px}nav a.active,nav a:hover{background:#286398;color:white}nav small{padding:12px}nav form{margin-top:auto}section{background:white;border:1px solid #e0e8f0;border-radius:15px;padding:22px;margin-bottom:18px;overflow-x:auto;box-shadow:0 3px 12px #173b5b08}h1{font-size:28px;margin:0 0 22px}h2{font-size:19px;margin-top:0}form{display:flex;gap:10px;flex-wrap:wrap;align-items:center}input,select,button{font:inherit;padding:11px 13px;border:1px solid #d0dce8;border-radius:8px;background:white;max-width:100%}button{background:#22649b;color:white;border:0;cursor:pointer;font-weight:600}button.danger{background:#b83242}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:12px;border-bottom:1px solid #e6edf4}th{background:#f3f7fb}details{border:1px solid #dce5ef;border-radius:10px;padding:13px;margin:10px 0}summary{cursor:pointer;font-weight:600}details form{margin-top:12px}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px}.metric{padding:16px;border:1px solid #dce5ef;border-radius:12px;background:#f7fafc}.metric b{display:block;font-size:24px;margin-top:10px}.notice{padding:12px;background:#eef4fa;border-left:4px solid #176a62}.export{display:inline-block;padding:10px;color:#176a62;font-weight:bold}.alert{color:#b83242}body:not(:has(nav)) main{max-width:480px;margin:8vh auto;background:white;border-radius:20px;padding:32px}body:not(:has(nav)) main input,body:not(:has(nav)) main button{width:100%}@media(max-width:800px){nav{position:static;width:100%;flex-direction:row;flex-wrap:wrap;padding:12px}nav img{width:130px;margin:0}nav a{padding:8px}body:has(nav) main,body:has(nav) header{margin-left:0}main{padding:14px}header{padding:12px}nav form{margin:0}section{padding:14px}}</style>'
PAGES={'dashboard': '{% for s in stores %}<section><h2>Magasin {{names[s]}}</h2>{% set articles=items|selectattr(\'store\',\'equalto\',s)|rejectattr(\'id\',\'in\',archived)|list %}{% set ns=namespace(value=0,alerts=0,pending=0) %}{% for i in articles %}{% set ns.value=ns.value+i.qty*(i.price or 0) %}{% if i.qty<=i.minimum %}{% set ns.alerts=ns.alerts+1 %}{% endif %}{% endfor %}{% for m in moves if m.item.store==s and m.status==\'attente\' %}{% set ns.pending=ns.pending+1 %}{% endfor %}<div class="metrics"><div class="metric"><span>Références</span><b>{{articles|length}}</b></div><div class="metric"><span>Valeur du stock (FCFA)</span><b>{{\'{:,.0f}\'.format(ns.value).replace(\',\', \' \')}}</b></div><div class="metric"><span>Articles en alerte</span><b>{{ns.alerts}}</b></div><div class="metric"><span>Validations en attente</span><b>{{ns.pending}}</b></div></div></section>{% endfor %}', 'articles': '{% if u.role in [\'admin\',\'magasinier\'] %}<section><h2>Nouvel article</h2><form method="post"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="action" value="item"><select name="store">{% for s in stores %}<option value="{{s}}">{{names[s]}}</option>{% endfor %}</select><input name="name" placeholder="Désignation" required><input name="category" placeholder="Catégorie"><input name="unit" placeholder="Unité" required><input name="minimum" type="number" min="0" step="0.001" placeholder="Seuil minimum" required><input name="price" type="number" min="0" step="0.01" placeholder="Prix FCFA" required><button>Créer</button></form></section>{% endif %}<section id="gestion"><h2>Gestion des articles</h2><input id="filterItems" placeholder="Rechercher un article" oninput="document.querySelectorAll(\'.article-row\').forEach(r=>r.hidden=!r.dataset.search.includes(this.value.toLowerCase()))">{% for s in stores %}<a class="export" href="/export?store={{s}}">Exporter {{names[s]}}</a>{% endfor %}<div class="article-list">{% for i in items %}<details class="article-row" data-search="{{(i.name ~ \' \' ~ i.category ~ \' \' ~ names[i.store])|lower}}"><summary>{{i.name}} · {{names[i.store]}} · {{i.qty}} {{i.unit}} {% if i.id in archived %} (archivé){% endif %}</summary>{% if u.role==\'admin\' %}<form method="post"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="id" value="{{i.id}}"><input name="name" value="{{i.name}}" required maxlength="200"><input name="category" value="{{i.category or \'\'}}"><input name="unit" value="{{i.unit}}" required><input type="number" name="minimum" min="0" step="0.001" value="{{i.minimum or 0}}" required><input type="number" name="price" min="0" step="0.01" value="{{i.price or 0}}" required><button name="action" value="edit_item">Enregistrer</button></form><form method="post"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="id" value="{{i.id}}">{% if i.id in archived %}<button name="action" value="restore_item">Réactiver</button>{% else %}<button name="action" value="archive_item">Archiver</button>{% endif %}<button class="danger" name="action" value="delete_item" onclick="return confirm(\'Supprimer cet article sans historique ?\')">Supprimer</button></form>{% endif %}</details>{% endfor %}</div></section>{% if u.role in [\'admin\',\'magasinier\'] %}<section id="import"><h2>Importer les articles Excel</h2><form method="post" enctype="multipart/form-data"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="action" value="import"><select name="store">{% for s in stores %}<option value="{{s}}">{{names[s]}}</option>{% endfor %}</select><input type="file" name="file" accept=".xlsx" required><button>Importer</button></form><small>Stock initial nul. Les entrées de stock nécessitent une validation.</small></section>{% endif %}', 'entrees': '{% if u.role in [\'admin\',\'magasinier\'] %}<section><h2>Nouvelle opération : {{ kind }}</h2><form method="post"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="action" value="move"><select name="item">{% for i in items if i.id not in archived %}<option value="{{i.id}}">{{names[i.store]}} — {{i.name}}</option>{% endfor %}</select><input type="hidden" name="kind" value="{{ kind }}"><input name="qty" type="number" min="0.001" step="0.001" required placeholder="Quantité"><select name="site"><option value="">Autre destination</option>{% for site in sites if site.id not in archived_sites %}<option value="{{site.id}}">{{names[site.store]}} — {{site.name}}</option>{% endfor %}</select><input name="destination" placeholder="Agent / fournisseur / précision"><button>Soumettre</button></form></section>{% endif %}<section><h2>Mouvements et validations</h2><table><tr><th>N°</th><th>Magasin / article</th><th>Opération</th><th>Destination</th><th>Statut</th><th>Traçabilité</th></tr>{% for m in moves if not kind or m.kind==kind %}<tr><td>{{m.id}}</td><td>{{names[m.item.store]}} / {{m.item.name}}</td><td>{{m.kind}} {{m.qty}}</td><td>{{m.destination}}</td><td>{{m.status}}{% if active==\'validations\' and m.status==\'attente\' and u.role in [\'admin\',\'validateur\'] and m.author!=u.name %}<form method="post"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="action" value="decide"><input type="hidden" name="move" value="{{m.id}}"><input name="reason" placeholder="Motif si rejet"><button name="decision" value="valide">Valider</button><button name="decision" value="rejete">Rejeter</button></form>{% endif %}</td><td>{{m.author}} · {{m.created}}<br>{{m.validator or \'\'}} {{m.decided or \'\'}} {{m.reason or \'\'}}</td></tr>{% endfor %}</table></section>', 'sorties': '{% if u.role in [\'admin\',\'magasinier\'] %}<section><h2>Nouvelle opération : {{ kind }}</h2><form method="post"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="action" value="move"><select name="item">{% for i in items if i.id not in archived %}<option value="{{i.id}}">{{names[i.store]}} — {{i.name}}</option>{% endfor %}</select><input type="hidden" name="kind" value="{{ kind }}"><input name="qty" type="number" min="0.001" step="0.001" required placeholder="Quantité"><select name="site"><option value="">Autre destination</option>{% for site in sites if site.id not in archived_sites %}<option value="{{site.id}}">{{names[site.store]}} — {{site.name}}</option>{% endfor %}</select><input name="destination" placeholder="Agent / fournisseur / précision"><button>Soumettre</button></form></section>{% endif %}<section><h2>Mouvements et validations</h2><table><tr><th>N°</th><th>Magasin / article</th><th>Opération</th><th>Destination</th><th>Statut</th><th>Traçabilité</th></tr>{% for m in moves if not kind or m.kind==kind %}<tr><td>{{m.id}}</td><td>{{names[m.item.store]}} / {{m.item.name}}</td><td>{{m.kind}} {{m.qty}}</td><td>{{m.destination}}</td><td>{{m.status}}{% if active==\'validations\' and m.status==\'attente\' and u.role in [\'admin\',\'validateur\'] and m.author!=u.name %}<form method="post"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="action" value="decide"><input type="hidden" name="move" value="{{m.id}}"><input name="reason" placeholder="Motif si rejet"><button name="decision" value="valide">Valider</button><button name="decision" value="rejete">Rejeter</button></form>{% endif %}</td><td>{{m.author}} · {{m.created}}<br>{{m.validator or \'\'}} {{m.decided or \'\'}} {{m.reason or \'\'}}</td></tr>{% endfor %}</table></section>', 'retours': '{% if u.role in [\'admin\',\'magasinier\'] %}<section><h2>Nouvelle opération : {{ kind }}</h2><form method="post"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="action" value="move"><select name="item">{% for i in items if i.id not in archived %}<option value="{{i.id}}">{{names[i.store]}} — {{i.name}}</option>{% endfor %}</select><input type="hidden" name="kind" value="{{ kind }}"><input name="qty" type="number" min="0.001" step="0.001" required placeholder="Quantité"><select name="site"><option value="">Autre destination</option>{% for site in sites if site.id not in archived_sites %}<option value="{{site.id}}">{{names[site.store]}} — {{site.name}}</option>{% endfor %}</select><input name="destination" placeholder="Agent / fournisseur / précision"><button>Soumettre</button></form></section>{% endif %}<section><h2>Mouvements et validations</h2><table><tr><th>N°</th><th>Magasin / article</th><th>Opération</th><th>Destination</th><th>Statut</th><th>Traçabilité</th></tr>{% for m in moves if not kind or m.kind==kind %}<tr><td>{{m.id}}</td><td>{{names[m.item.store]}} / {{m.item.name}}</td><td>{{m.kind}} {{m.qty}}</td><td>{{m.destination}}</td><td>{{m.status}}{% if active==\'validations\' and m.status==\'attente\' and u.role in [\'admin\',\'validateur\'] and m.author!=u.name %}<form method="post"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="action" value="decide"><input type="hidden" name="move" value="{{m.id}}"><input name="reason" placeholder="Motif si rejet"><button name="decision" value="valide">Valider</button><button name="decision" value="rejete">Rejeter</button></form>{% endif %}</td><td>{{m.author}} · {{m.created}}<br>{{m.validator or \'\'}} {{m.decided or \'\'}} {{m.reason or \'\'}}</td></tr>{% endfor %}</table></section>', 'rebuts': '{% if u.role in [\'admin\',\'magasinier\'] %}<section><h2>Nouvelle opération : {{ kind }}</h2><form method="post"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="action" value="move"><select name="item">{% for i in items if i.id not in archived %}<option value="{{i.id}}">{{names[i.store]}} — {{i.name}}</option>{% endfor %}</select><input type="hidden" name="kind" value="{{ kind }}"><input name="qty" type="number" min="0.001" step="0.001" required placeholder="Quantité"><select name="site"><option value="">Autre destination</option>{% for site in sites if site.id not in archived_sites %}<option value="{{site.id}}">{{names[site.store]}} — {{site.name}}</option>{% endfor %}</select><input name="destination" placeholder="Agent / fournisseur / précision"><button>Soumettre</button></form></section>{% endif %}<section><h2>Mouvements et validations</h2><table><tr><th>N°</th><th>Magasin / article</th><th>Opération</th><th>Destination</th><th>Statut</th><th>Traçabilité</th></tr>{% for m in moves if not kind or m.kind==kind %}<tr><td>{{m.id}}</td><td>{{names[m.item.store]}} / {{m.item.name}}</td><td>{{m.kind}} {{m.qty}}</td><td>{{m.destination}}</td><td>{{m.status}}{% if active==\'validations\' and m.status==\'attente\' and u.role in [\'admin\',\'validateur\'] and m.author!=u.name %}<form method="post"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="action" value="decide"><input type="hidden" name="move" value="{{m.id}}"><input name="reason" placeholder="Motif si rejet"><button name="decision" value="valide">Valider</button><button name="decision" value="rejete">Rejeter</button></form>{% endif %}</td><td>{{m.author}} · {{m.created}}<br>{{m.validator or \'\'}} {{m.decided or \'\'}} {{m.reason or \'\'}}</td></tr>{% endfor %}</table></section>', 'validations': '<section><h2>Mouvements et validations</h2><table><tr><th>N°</th><th>Magasin / article</th><th>Opération</th><th>Destination</th><th>Statut</th><th>Traçabilité</th></tr>{% for m in moves if not kind or m.kind==kind %}<tr><td>{{m.id}}</td><td>{{names[m.item.store]}} / {{m.item.name}}</td><td>{{m.kind}} {{m.qty}}</td><td>{{m.destination}}</td><td>{{m.status}}{% if active==\'validations\' and m.status==\'attente\' and u.role in [\'admin\',\'validateur\'] and m.author!=u.name %}<form method="post"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="action" value="decide"><input type="hidden" name="move" value="{{m.id}}"><input name="reason" placeholder="Motif si rejet"><button name="decision" value="valide">Valider</button><button name="decision" value="rejete">Rejeter</button></form>{% endif %}</td><td>{{m.author}} · {{m.created}}<br>{{m.validator or \'\'}} {{m.decided or \'\'}} {{m.reason or \'\'}}</td></tr>{% endfor %}</table></section>', 'sites': '<section id="sites"><h2>Sites et bénéficiaires</h2>{% if u.role==\'admin\' %}<form method="post"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="action" value="site"><select name="store">{% for s in stores %}<option value="{{s}}">{{names[s]}}</option>{% endfor %}</select><input name="name" required placeholder="Nom du site"><input name="address" placeholder="Adresse"><button>Créer le site</button></form>{% endif %}<table><tr><th>Site</th><th>Magasin</th><th>Adresse</th></tr>{% for site in sites %}<tr><td>{{site.name}}</td><td>{{names[site.store]}}</td><td>{{site.address}}</td></tr>{% endfor %}</table></section>{% if u.role==\'admin\' %}<section id="gestion-sites"><h2>Modifier les sites</h2>{% for site in sites %}<details><summary>{{site.name}} · {{names[site.store]}} {% if site.id in archived_sites %}(inactif){% endif %}</summary><form method="post"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="id" value="{{site.id}}"><input name="name" value="{{site.name}}" required><input name="address" value="{{site.address or \'\'}}"><button name="action" value="edit_site">Enregistrer</button><button name="action" value="{{\'restore_site\' if site.id in archived_sites else \'archive_site\'}}">{{\'Réactiver\' if site.id in archived_sites else \'Désactiver\'}}</button></form></details>{% endfor %}</section>{% endif %}', 'notifications': '<section id="notifications"><h2>Notifications</h2><p>Alertes actualisées à chaque ouverture de page.</p>{% for m in moves if m.status==\'attente\' and u.role in [\'admin\',\'validateur\'] and m.author!=u.name %}<p class="notice">Validation requise · {{m.item.name}} · {{m.kind}} {{m.qty}}</p>{% endfor %}{% for m in moves if m.author==u.name and m.status!=\'attente\' %}<p class="notice">Mouvement n°{{m.id}} · {{m.status}} · {{m.reason or \'\'}}</p>{% endfor %}{% for i in items if i.id not in archived and i.qty<=i.minimum %}<p class="notice">Stock faible · {{names[i.store]}} · {{i.name}} ({{i.qty}})</p>{% endfor %}</section>', 'comptes': '{% if u.role==\'admin\' %}<section><h2>Créer un utilisateur</h2><form method="post"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="action" value="user"><input name="name" required placeholder="Identifiant"><input name="password" type="password" minlength="12" required placeholder="Mot de passe"><select name="role"><option>magasinier</option><option>validateur</option><option>lecture</option><option>admin</option></select><select name="store"><option value="0">Les deux</option><option value="1">Gardiennage</option><option value="2">Nettoyage</option></select><button>Créer</button></form></section><section id="comptes"><h2>Gestion des comptes</h2>{% for target in users if target.id!=u.id and (u.store==0 or target.store==u.store) %}<details><summary>{{target.name}} · {{target.role}} {% if target.id in disabled %}(désactivé){% endif %}</summary><form method="post"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="id" value="{{target.id}}"><input type="password" name="password" minlength="12" required placeholder="Nouveau mot de passe"><button name="action" value="reset_user">Réinitialiser</button></form><form method="post"><input type="hidden" name="csrf" value="{{ session.csrf }}"><input type="hidden" name="id" value="{{target.id}}"><button name="action" value="{{\'enable_user\' if target.id in disabled else \'disable_user\'}}">{{\'Réactiver\' if target.id in disabled else \'Désactiver\'}}</button></form></details>{% endfor %}</section>{% endif %}', 'rapports': '<section><h2>Exports de stock</h2>{% for s in stores %}<p><a class="export" href="/export?store={{s}}">Télécharger le stock {{names[s]}} (Excel)</a></p>{% endfor %}</section>'}
TITLES={'dashboard': 'Tableau de bord', 'articles': 'Articles', 'entrees': 'Entrées', 'sorties': 'Sorties', 'retours': 'Retours', 'rebuts': 'Mises au rebut', 'validations': 'Validations', 'sites': 'Sites', 'notifications': 'Notifications', 'comptes': 'Comptes', 'rapports': 'Rapports'}
KINDS={'entrees': 'entrée', 'sorties': 'sortie', 'retours': 'retour', 'rebuts': 'rebut'}
NAV='<nav><img src="/static/logo.png" alt="GROUPE SABA"><small>GESTION DES MAGASINS</small>{% for key,label in menu.items() if key != "comptes" or u.role == "admin" %}<a href="{{url_for(\'index\',active=key)}}" class="{{\'active\' if active==key else \'\'}}">{{label}}</a>{% endfor %}<small>{{u.name}} · {{u.role}}</small><form action="/logout" method="post"><input type="hidden" name="csrf" value="{{session.csrf}}"><button>Déconnexion</button></form></nav>'
def page(title,body,**ctx):
 return render_template_string('<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+'</title>'+STYLE+'<header><img src="/static/logo.png" alt="GROUPE SABA"><strong>Gestion des magasins</strong></header><main>{% for m in get_flashed_messages() %}<p class="notice">{{m}}</p>{% endfor %}'+(NAV+'<h1>{{heading}}</h1>'+body[len(NAV):] if body.startswith(NAV) else '<h1>{{heading}}</h1>'+body)+'</main></html>',heading=title,menu=TITLES,**ctx)

if __name__=='__main__': app.run()
