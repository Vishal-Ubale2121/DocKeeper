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
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            original_name TEXT NOT NULL,
            name TEXT,
            description TEXT,
            file_type TEXT,
            upload_date TEXT,
            user_id INTEGER
        )
    ''')
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

def get_metadata():
    db = get_db()
    docs = db.execute('SELECT * FROM documents').fetchall()
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
@login_required
@admin_required
def dashboard():
    docs = get_metadata()
    return render_template('dashboard.html', docs=docs)

@app.route('/upload', methods=['GET', 'POST'])
@login_required
@admin_required
def upload():
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
            
            if not os.path.exists(app.config['UPLOAD_FOLDER']):
                os.makedirs(app.config['UPLOAD_FOLDER'])
                
            with open(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename), 'wb') as f:
                f.write(encrypted_data)
            
            file_ext = os.path.splitext(filename)[1].lower()
            if file_ext in ['.jpg', '.jpeg', '.png', '.gif']:
                file_type = 'image'
            elif file_ext in ['.mp4', '.mov', '.avi']:
                file_type = 'video'
            else:
                file_type = 'file'

            db = get_db()
            cursor = db.execute('''
                INSERT INTO documents (filename, original_name, name, description, file_type, upload_date, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (unique_filename, filename, name, description, file_type, 
                  datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), current_user.id))
            db.commit()
            db.close()
            return redirect(url_for('dashboard'))
            
    return render_template('upload.html')

@app.route('/view/<int:doc_id>')
@login_required
@admin_required
def view_file(doc_id):
    db = get_db()
    doc = db.execute('SELECT * FROM documents WHERE id = ?', (doc_id,)).fetchone()
    db.close()
    if not doc:
        return "File not found", 404
    
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], doc['filename'])
    
    if not os.path.exists(file_path):
        return "File not found on disk", 404
        
    try:
        with open(file_path, 'rb') as f:
            encrypted_data = f.read()
        
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
    
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], doc['filename'])
    
    if not os.path.exists(file_path):
        return "File not found on disk", 404
        
    try:
        with open(file_path, 'rb') as f:
            encrypted_data = f.read()
        
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
    
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], doc['filename'])
    if os.path.exists(file_path):
        os.remove(file_path)
    
    db.execute('DELETE FROM documents WHERE id = ?', (doc_id,))
    db.commit()
    db.close()
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    init_db()
    app.run(debug=True)
