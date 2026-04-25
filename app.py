import os
import io
import json
import uuid
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, Response, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
from cryptography.fernet import Fernet
from functools import wraps
import mimetypes
import drive_service

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-12345')
app.config['FERNET_KEY'] = os.environ.get('FERNET_KEY', 'tV_Hw7LFaSqFfCFhv-GW8e_k3Arp2mXRMSJrEOeD3eo=')

# More robust path handling for server environments
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'uploads')
app.config['METADATA_FILE'] = os.path.join(app.config['UPLOAD_FOLDER'], 'metadata.json')

# Database Config (Supports Render's Postgres and Local SQLite)
db_url = os.environ.get('DATABASE_URL', 'sqlite:///' + os.path.join(BASE_DIR, 'db.sqlite'))
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB limit

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

fernet = Fernet(app.config['FERNET_KEY'])

# Ensure upload folder exists
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# --- Models ---
class User(UserMixin):
    def __init__(self, id, username, password):
        self.id = id
        self.username = username
        self.password = password

# Hardcoded Admin User
ADMIN_USERNAME = 'vish2121'
ADMIN_USER = User(id=1, username=ADMIN_USERNAME, password=generate_password_hash('53721@Docs'))

class Folder(db.Model):
    __tablename__ = 'folders'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('folders.id'))
    drive_folder_id = db.Column(db.String)
    user_id = db.Column(db.Integer)
    
    subfolders = db.relationship('Folder', backref=db.backref('parent', remote_side=[id]), cascade="all, delete-orphan")
    documents = db.relationship('Document', backref='folder', cascade="all, delete-orphan")

class Document(db.Model):
    __tablename__ = 'documents'
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String, nullable=False)
    original_name = db.Column(db.String, nullable=False)
    name = db.Column(db.String)
    description = db.Column(db.Text)
    file_type = db.Column(db.String)
    upload_date = db.Column(db.String)
    user_id = db.Column(db.Integer)
    folder_id = db.Column(db.Integer, db.ForeignKey('folders.id'))

# Ensure db tables are created
with app.app_context():
    db.create_all()

# --- Decorators ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.username != ADMIN_USERNAME:
            flash('Admin access required.')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_folders(folder_id=None):
    if folder_id:
        folders = Folder.query.filter_by(parent_id=folder_id).all()
    else:
        folders = Folder.query.filter_by(parent_id=None).all()
    return [{"id": f.id, "name": f.name, "parent_id": f.parent_id, "drive_folder_id": f.drive_folder_id} for f in folders]

def get_metadata(folder_id=None):
    if folder_id:
        docs = Document.query.filter_by(folder_id=folder_id).all()
    else:
        docs = Document.query.filter_by(folder_id=None).all()
    return [{"id": d.id, "filename": d.filename, "original_name": d.original_name, "name": d.name, "description": d.description, "file_type": d.file_type, "upload_date": d.upload_date, "folder_id": d.folder_id} for d in docs]

@login_manager.user_loader
def load_user(user_id):
    if str(user_id) == '1':
        return ADMIN_USER
    return None

# --- Routes ---
@app.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.username == ADMIN_USERNAME:
            return redirect(url_for('dashboard'))
        logout_user()
    return render_template('login.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_USERNAME and check_password_hash(ADMIN_USER.password, password):
            login_user(ADMIN_USER)
            return redirect(url_for('dashboard'))
        flash('Invalid username or password')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    # Disable registration for security
    flash('Registration is disabled. Only Admin can access.')
    return redirect(url_for('login'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@app.route('/dashboard/<int:folder_id>')
@login_required
@admin_required
def dashboard(folder_id=None):
    docs = get_metadata(folder_id)
    folders = get_folders(folder_id)
    
    # Generate breadcrumbs
    breadcrumbs = []
    curr_id = folder_id
    while curr_id:
        f = Folder.query.get(curr_id)
        if f:
            breadcrumbs.insert(0, {'id': f.id, 'name': f.name})
            curr_id = f.parent_id
        else:
            break
            
    return render_template('dashboard.html', docs=docs, folders=folders, current_folder_id=folder_id, breadcrumbs=breadcrumbs)

@app.route('/upload', methods=['GET', 'POST'])
@login_required
@admin_required
def upload():
    folder_id = request.args.get('folder_id') or request.form.get('folder_id')
    try:
        folder_id = int(folder_id) if folder_id else None
    except ValueError:
        folder_id = None
            
    if request.method == 'POST':
        file = request.files.get('file')
        name = request.form.get('name')
        description = request.form.get('description')
        
        if file and file.filename:
            filename = secure_filename(file.filename)
            unique_filename = f"{datetime.now().timestamp()}_{filename}"
            
            # Encrypt file content
            file_data = file.read()
            encrypted_data = fernet.encrypt(file_data)
            
            # Determine mime type before upload
            mime_type, _ = mimetypes.guess_type(filename)
            if not mime_type:
                mime_type = 'application/octet-stream'
                
            # Find drive folder id
            drive_folder_id = None
            if folder_id:
                folder = Folder.query.get(folder_id)
                if folder:
                    drive_folder_id = folder.drive_folder_id
                
            # Upload to Google Drive
            drive_file_id = drive_service.upload_file(unique_filename, encrypted_data, mime_type=mime_type, parent_id=drive_folder_id)
            
            if not drive_file_id:
                flash('Error uploading file to Google Drive')
                return redirect(url_for('dashboard', folder_id=folder_id))
            
            file_ext = os.path.splitext(filename)[1].lower()
            if file_ext in ['.jpg', '.jpeg', '.png', '.gif']:
                file_type = 'image'
            elif file_ext in ['.mp4', '.mov', '.avi']:
                file_type = 'video'
            else:
                file_type = 'file'

            new_doc = Document(
                filename=drive_file_id,
                original_name=filename,
                name=name,
                description=description,
                file_type=file_type,
                upload_date=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                user_id=current_user.id,
                folder_id=folder_id
            )
            db.session.add(new_doc)
            db.session.commit()
            
            return redirect(url_for('dashboard', folder_id=folder_id))
            
    return render_template('upload.html', folder_id=folder_id)

@app.route('/view/<int:doc_id>')
@login_required
@admin_required
def view_file(doc_id):
    doc = Document.query.get(doc_id)
    if not doc:
        return "File not found", 404
    
    try:
        encrypted_data = drive_service.download_file(doc.filename)
        if not encrypted_data:
            return "Error downloading file from Google Drive", 500
            
        decrypted_data = fernet.decrypt(encrypted_data)
        mime_type, _ = mimetypes.guess_type(doc.original_name)
        
        return send_file(
            io.BytesIO(decrypted_data),
            mimetype=mime_type or 'application/octet-stream'
        )
    except Exception as e:
        app.logger.error(f"Error serving file {doc_id}: {e}")
        return f"Error opening file", 500

@app.route('/download/<int:doc_id>')
@login_required
@admin_required
def download(doc_id):
    doc = Document.query.get(doc_id)
    if not doc:
        return "File not found", 404
    
    try:
        encrypted_data = drive_service.download_file(doc.filename)
        if not encrypted_data:
            return "Error downloading file from Google Drive", 500
            
        decrypted_data = fernet.decrypt(encrypted_data)
        
        return send_file(
            io.BytesIO(decrypted_data),
            as_attachment=True,
            download_name=doc.original_name
        )
    except Exception as e:
        app.logger.error(f"Error downloading file {doc_id}: {e}")
        return "Error downloading file", 500

@app.route('/delete/<int:doc_id>', methods=['POST'])
@login_required
@admin_required
def delete(doc_id):
    doc = Document.query.get(doc_id)
    if not doc:
        return "File not found", 404
    
    success = drive_service.delete_file(doc.filename)
    if not success:
        app.logger.warning(f"Failed to delete file {doc.filename} from Google Drive")
    
    db.session.delete(doc)
    db.session.commit()
    return redirect(url_for('dashboard'))

def recursive_delete_folder(folder_id):
    # This logic can be simplified heavily using SQLAlchemy cascade='all, delete' 
    # but since files are on Google Drive we still need to delete them explicitly
    
    folder = Folder.query.get(folder_id)
    if not folder:
        return
        
    # Delete docs from Drive
    for doc in folder.documents:
        drive_service.delete_file(doc.filename)
        
    # Recurse for subfolders
    for subfolder in folder.subfolders:
        recursive_delete_folder(subfolder.id)
        
    # Delete folder from drive
    if folder.drive_folder_id:
        drive_service.delete_file(folder.drive_folder_id)
        
    # SQLAlchemy will handle deleting child records locally because of cascade
    db.session.delete(folder)

@app.route('/create_folder', methods=['POST'])
@login_required
@admin_required
def create_folder():
    name = request.form.get('name')
    parent_id = request.form.get('parent_id')
    try:
        parent_id = int(parent_id) if parent_id else None
    except ValueError:
        parent_id = None
            
    if name:
        drive_parent_id = None
        if parent_id:
            parent_folder = Folder.query.get(parent_id)
            if parent_folder:
                drive_parent_id = parent_folder.drive_folder_id
                
        drive_folder_id = drive_service.create_folder(name, drive_parent_id)
        
        if drive_folder_id:
            new_folder = Folder(
                name=name,
                parent_id=parent_id,
                drive_folder_id=drive_folder_id,
                user_id=current_user.id
            )
            db.session.add(new_folder)
            db.session.commit()
        
    return redirect(url_for('dashboard', folder_id=parent_id))

@app.route('/edit_folder/<int:folder_id>', methods=['POST'])
@login_required
@admin_required
def edit_folder(folder_id):
    name = request.form.get('name')
    if name:
        folder = Folder.query.get(folder_id)
        if folder:
            if folder.drive_folder_id:
                drive_service.rename_file(folder.drive_folder_id, name)
            folder.name = name
            db.session.commit()
            return redirect(url_for('dashboard', folder_id=folder.parent_id))
    return redirect(url_for('dashboard'))

@app.route('/delete_folder/<int:folder_id>', methods=['POST'])
@login_required
@admin_required
def delete_folder(folder_id):
    folder = Folder.query.get(folder_id)
    if folder:
        parent_id = folder.parent_id
        recursive_delete_folder(folder_id)
        db.session.commit()
        return redirect(url_for('dashboard', folder_id=parent_id))
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    app.run(debug=True)
