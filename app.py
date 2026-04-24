import os
import io
import json
import uuid
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, Response, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
from cryptography.fernet import Fernet
from functools import wraps
import mimetypes
import sqlite3
import drive_service

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-key-12345'
app.config['FERNET_KEY'] = 'tV_Hw7LFaSqFfCFhv-GW8e_k3Arp2mXRMSJrEOeD3eo='

# More robust path handling for server environments
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'uploads')
app.config['METADATA_FILE'] = os.path.join(app.config['UPLOAD_FOLDER'], 'metadata.json')
app.config['DATABASE'] = os.path.join(BASE_DIR, 'db.sqlite')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB limit

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

fernet = Fernet(app.config['FERNET_KEY'])

# Ensure upload folder exists and DB is initialized at module level
# This ensures it runs on Render/Gunicorn where __name__ == "__main__" is false
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])
# --- Models (Replacing DB with simple classes and JSON) ---
class User(UserMixin):
    def __init__(self, id, username, password):
        self.id = id
        self.username = username
        self.password = password

# Hardcoded Admin User
ADMIN_USERNAME = 'vish2121'
# Actually, since it's just one user, I can just check the password directly or generate a hash.
# Let's use a simpler approach for now and fix the hash in a moment.
ADMIN_USER = User(id=1, username=ADMIN_USERNAME, password=generate_password_hash('53721@Docs'))

# --- Decorators ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.username != ADMIN_USERNAME:
            flash('Admin access required.')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_db():
    db = sqlite3.connect(app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.execute('''
        CREATE TABLE IF NOT EXISTS folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            parent_id INTEGER,
            drive_folder_id TEXT,
            user_id INTEGER,
            FOREIGN KEY (parent_id) REFERENCES folders (id)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            original_name TEXT NOT NULL,
            name TEXT,
            description TEXT,
            file_type TEXT,
            upload_date TEXT,
            user_id INTEGER,
            folder_id INTEGER,
            FOREIGN KEY (folder_id) REFERENCES folders (id)
        )
    ''')
    try:
        db.execute('ALTER TABLE documents ADD COLUMN folder_id INTEGER REFERENCES folders(id)')
    except sqlite3.OperationalError:
        pass
    db.commit()
    
    # Migrate from JSON if exists
    if os.path.exists(app.config['METADATA_FILE']):
        print("Migrating metadata from JSON to SQLite...")
        with open(app.config['METADATA_FILE'], 'r') as f:
            try:
                data = json.load(f)
                for doc in data:
                    # Check if already migrated
                    exists = db.execute('SELECT id FROM documents WHERE id = ?', (doc['id'],)).fetchone()
                    if not exists:
                        db.execute('''
                            INSERT INTO documents (id, filename, original_name, name, description, file_type, upload_date, user_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (doc['id'], doc['filename'], doc['original_name'], doc.get('name'), 
                              doc.get('description'), doc.get('file_type'), doc.get('upload_date'), doc.get('user_id')))
                db.commit()
                # Rename old file instead of deleting to be safe
                os.rename(app.config['METADATA_FILE'], app.config['METADATA_FILE'] + '.bak')
            except Exception as e:
                print(f"Migration error: {e}")
    db.close()

def get_folders(folder_id=None):
    db = get_db()
    if folder_id:
        folders = db.execute('SELECT * FROM folders WHERE parent_id = ?', (folder_id,)).fetchall()
    else:
        folders = db.execute('SELECT * FROM folders WHERE parent_id IS NULL').fetchall()
    db.close()
    return [dict(f) for f in folders]

def get_metadata(folder_id=None):
    db = get_db()
    if folder_id:
        docs = db.execute('SELECT * FROM documents WHERE folder_id = ?', (folder_id,)).fetchall()
    else:
        docs = db.execute('SELECT * FROM documents WHERE folder_id IS NULL').fetchall()
    db.close()
    return [dict(doc) for doc in docs]

@login_manager.user_loader
def load_user(user_id):
    if user_id == '1':
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
    db = get_db()
    curr_id = folder_id
    while curr_id:
        f = db.execute('SELECT id, name, parent_id FROM folders WHERE id = ?', (curr_id,)).fetchone()
        if f:
            breadcrumbs.insert(0, {'id': f['id'], 'name': f['name']})
            curr_id = f['parent_id']
        else:
            break
    db.close()
    
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
                db = get_db()
                folder = db.execute('SELECT drive_folder_id FROM folders WHERE id = ?', (folder_id,)).fetchone()
                if folder:
                    drive_folder_id = folder['drive_folder_id']
                db.close()
                
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

            db = get_db()
            cursor = db.execute('''
                INSERT INTO documents (filename, original_name, name, description, file_type, upload_date, user_id, folder_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (drive_file_id, filename, name, description, file_type, 
                  datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), current_user.id, folder_id))
            db.commit()
            db.close()
            return redirect(url_for('dashboard', folder_id=folder_id))
            
    return render_template('upload.html', folder_id=folder_id)

@app.route('/view/<int:doc_id>')
@login_required
@admin_required
def view_file(doc_id):
    db = get_db()
    doc = db.execute('SELECT * FROM documents WHERE id = ?', (doc_id,)).fetchone()
    db.close()
    if not doc:
        return "File not found", 404
    
    try:
        encrypted_data = drive_service.download_file(doc['filename'])
        if not encrypted_data:
            return "Error downloading file from Google Drive", 500
            
        decrypted_data = fernet.decrypt(encrypted_data)
        mime_type, _ = mimetypes.guess_type(doc['original_name'])
        
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
    db = get_db()
    doc = db.execute('SELECT * FROM documents WHERE id = ?', (doc_id,)).fetchone()
    db.close()
    if not doc:
        return "File not found", 404
    
    try:
        encrypted_data = drive_service.download_file(doc['filename'])
        if not encrypted_data:
            return "Error downloading file from Google Drive", 500
            
        decrypted_data = fernet.decrypt(encrypted_data)
        
        return send_file(
            io.BytesIO(decrypted_data),
            as_attachment=True,
            download_name=doc['original_name']
        )
    except Exception as e:
        app.logger.error(f"Error downloading file {doc_id}: {e}")
        return "Error downloading file", 500

@app.route('/delete/<int:doc_id>', methods=['POST'])
@login_required
@admin_required
def delete(doc_id):
    db = get_db()
    doc = db.execute('SELECT * FROM documents WHERE id = ?', (doc_id,)).fetchone()
    if not doc:
        db.close()
        return "File not found", 404
    
    success = drive_service.delete_file(doc['filename'])
    if not success:
        app.logger.warning(f"Failed to delete file {doc['filename']} from Google Drive")
    
    db.execute('DELETE FROM documents WHERE id = ?', (doc_id,))
    db.commit()
    db.close()
    return redirect(url_for('dashboard'))

def recursive_delete_folder(db, folder_id):
    # Delete subfolders
    subfolders = db.execute('SELECT id FROM folders WHERE parent_id = ?', (folder_id,)).fetchall()
    for sf in subfolders:
        recursive_delete_folder(db, sf['id'])
    
    # Delete docs
    docs = db.execute('SELECT id, filename FROM documents WHERE folder_id = ?', (folder_id,)).fetchall()
    for doc in docs:
        drive_service.delete_file(doc['filename'])
        db.execute('DELETE FROM documents WHERE id = ?', (doc['id'],))
        
    # Delete folder from drive and db
    f = db.execute('SELECT drive_folder_id FROM folders WHERE id = ?', (folder_id,)).fetchone()
    if f and f['drive_folder_id']:
        drive_service.delete_file(f['drive_folder_id'])
    db.execute('DELETE FROM folders WHERE id = ?', (folder_id,))

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
        db = get_db()
        drive_parent_id = None
        if parent_id:
            parent_folder = db.execute('SELECT drive_folder_id FROM folders WHERE id = ?', (parent_id,)).fetchone()
            if parent_folder:
                drive_parent_id = parent_folder['drive_folder_id']
                
        drive_folder_id = drive_service.create_folder(name, drive_parent_id)
        
        if drive_folder_id:
            db.execute('''
                INSERT INTO folders (name, parent_id, drive_folder_id, user_id)
                VALUES (?, ?, ?, ?)
            ''', (name, parent_id, drive_folder_id, current_user.id))
            db.commit()
        db.close()
        
    return redirect(url_for('dashboard', folder_id=parent_id))

@app.route('/edit_folder/<int:folder_id>', methods=['POST'])
@login_required
@admin_required
def edit_folder(folder_id):
    name = request.form.get('name')
    if name:
        db = get_db()
        folder = db.execute('SELECT parent_id, drive_folder_id FROM folders WHERE id = ?', (folder_id,)).fetchone()
        if folder:
            if folder['drive_folder_id']:
                drive_service.rename_file(folder['drive_folder_id'], name)
            db.execute('UPDATE folders SET name = ? WHERE id = ?', (name, folder_id))
            db.commit()
        parent_id = folder['parent_id'] if folder else None
        db.close()
        return redirect(url_for('dashboard', folder_id=parent_id))
    return redirect(url_for('dashboard'))

@app.route('/delete_folder/<int:folder_id>', methods=['POST'])
@login_required
@admin_required
def delete_folder(folder_id):
    db = get_db()
    folder = db.execute('SELECT parent_id FROM folders WHERE id = ?', (folder_id,)).fetchone()
    parent_id = folder['parent_id'] if folder else None
    
    recursive_delete_folder(db, folder_id)
    db.commit()
    db.close()
    
    return redirect(url_for('dashboard', folder_id=parent_id))

init_db()

if __name__ == '__main__':
    app.run(debug=True)
