from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app, jsonify, Response
from supabase import PostgrestAPIError
from app import supabase
from app.decorators import login_required, roles_required, admin_required
from app.forms import ApproveUserForm, TemplateEditForm
from werkzeug.utils import secure_filename
import re
from docx import Document
import io, os, uuid
from io import BytesIO
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
import html
from supabase import Client, create_client
import platform
import requests
from datetime import datetime
import jwt
import time


admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# Configuration
STORAGE_BUCKET_NAME = 'clp_files'
TEMPLATE_KEY = "PBSIT/PBSIT-001-LP-20242.docx"

# ONLYOFFICE JWT Secret - IMPORTANT: Set this in your .env file
# If not set, JWT will be disabled (less secure but simpler)
ONLYOFFICE_JWT_SECRET = os.getenv("ONLYOFFICE_JWT_SECRET", "")

def generate_jwt_token(payload):
    """Generate JWT token for ONLYOFFICE if JWT secret is configured"""
    if not ONLYOFFICE_JWT_SECRET:
        return None
    
    try:
        token = jwt.encode(payload, ONLYOFFICE_JWT_SECRET, algorithm='HS256')
        return token
    except Exception as e:
        current_app.logger.error(f"Error generating JWT: {e}")
        return None

@admin_bp.route('/serve_document/<doc_key>')
def serve_document(doc_key):
    """Serve the document to ONLYOFFICE (acts as a proxy)
    
    NOTE: No @login_required because ONLYOFFICE needs to access this.
    For security, you could validate the doc_key or check IP address.
    """
    try:
        current_app.logger.info(f"Serving document with key: {doc_key}")
        
        # Download from Supabase
        template_bytes = supabase.storage.from_(STORAGE_BUCKET_NAME).download(TEMPLATE_KEY)
        
        if not template_bytes:
            current_app.logger.error("Document not found in Supabase")
            abort(404, description="Document not found")
        
        current_app.logger.info(f"Successfully downloaded document, size: {len(template_bytes)} bytes")
        
        return Response(
            template_bytes,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            headers={
                "Content-Disposition": "inline; filename=template.docx",
                "Content-Length": str(len(template_bytes)),
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-cache"
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error serving document: {e}")
        abort(500, description=f"Error serving document: {str(e)}")


@admin_bp.route('/test_connectivity')
def test_connectivity():
    """Test endpoint to verify ONLYOFFICE can reach Flask
    
    NOTE: No authentication for testing purposes
    """
    import platform as plat
    return jsonify({
        "status": "ok",
        "message": "Flask is reachable from ONLYOFFICE",
        "platform": plat.system(),
        "timestamp": str(datetime.now())
    })


@admin_bp.route('/debug_onlyoffice')
@login_required
@admin_required
def debug_onlyoffice():
    """Debug page to test ONLYOFFICE connectivity"""
    import socket
    
    # Get various network addresses
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except:
        local_ip = "Unable to determine"
    
    if platform.system() == "Windows":
        docker_host = "http://host.docker.internal:5000"
    else:
        docker_host = "http://172.17.0.1:5000"
    
    browser_host = request.host_url.rstrip('/')
    
    # Generate a test key
    import hashlib
    test_key = hashlib.md5(b"test").hexdigest()
    
    debug_info = {
        "hostname": hostname,
        "local_ip": local_ip,
        "platform": platform.system(),
        "docker_host": docker_host,
        "browser_host": browser_host,
        "request_host": request.host,
        "test_urls": {
            "serve_document": f"{browser_host}/admin/serve_document/{test_key}",
            "test_connectivity": f"{browser_host}/admin/test_connectivity",
            "docker_serve_document": f"{docker_host}/admin/serve_document/{test_key}",
        },
        "onlyoffice_config": {
            "jwt_enabled": bool(ONLYOFFICE_JWT_SECRET),
            "jwt_secret_set": "Yes" if ONLYOFFICE_JWT_SECRET else "No"
        }
    }
    
    return jsonify(debug_info)


@admin_bp.route('/manage_template')
@login_required
@admin_required
def manage_template():
    """Open the DOCX in ONLYOFFICE editor."""
    try:
        # Get file metadata from Supabase to generate a consistent key
        file_info = supabase.storage.from_(STORAGE_BUCKET_NAME).list(path="PBSIT")
        
        # Find our specific file
        template_file = next((f for f in file_info if f['name'] == 'PBSIT-001-LP-20242.docx'), None)
        
        if template_file and 'updated_at' in template_file:
            # Create a stable key based on file path and last modified time
            import hashlib
            key_string = f"{TEMPLATE_KEY}_{template_file['updated_at']}"
            doc_key = hashlib.md5(key_string.encode()).hexdigest()
        else:
            # Fallback: use a fixed key for this template
            import hashlib
            doc_key = hashlib.md5(TEMPLATE_KEY.encode()).hexdigest()
        
        # OPTION 1: Use Supabase public URL (simpler, works immediately)
        # Make sure your bucket/file has public access enabled
        supabase_url = os.getenv("SUPABASE_URL")
        doc_url_direct = f"{supabase_url}/storage/v1/object/public/{STORAGE_BUCKET_NAME}/{TEMPLATE_KEY}"
        
        # OPTION 2: Use Flask proxy (more control, but needs networking setup)
        # Determine host URL for ONLYOFFICE (from inside Docker)
        if platform.system() == "Windows":
            onlyoffice_host = "http://host.docker.internal:5000"
        else:
            onlyoffice_host = "http://172.17.0.1:5000"
        
        # Get the actual host for browser requests
        browser_host = request.host_url.rstrip('/')
        if not browser_host or browser_host == 'http://':
            browser_host = "http://localhost:5000"
        
        # Document URLs
        doc_url_proxy = f"{onlyoffice_host}/admin/serve_document/{doc_key}"
        doc_url_for_browser = f"{browser_host}/admin/serve_document/{doc_key}"
        
        # CHOOSE WHICH METHOD TO USE
        # Start with direct Supabase URL - it's simpler and should work immediately
        use_direct_url = True  # Change to False to use Flask proxy
        
        if use_direct_url:
            doc_url = doc_url_direct
            current_app.logger.info("Using direct Supabase URL")
        else:
            doc_url = doc_url_proxy
            current_app.logger.info("Using Flask proxy URL")
        
        # Callback URL for saving
        ngrok_url = os.getenv("NGROK_URL", "")  # e.g., "https://abc123.ngrok.io"
        if ngrok_url:
            callback_url = f"{ngrok_url}/admin/onlyoffice_callback"
            current_app.logger.info(f"Using ngrok callback URL: {callback_url}")
        else:
            callback_url = f"{onlyoffice_host}/admin/onlyoffice_callback"
            current_app.logger.info(f"Using Docker callback URL: {callback_url}")
        
        # Prepare config for ONLYOFFICE
        config = {
            "document": {
                "title": "CLP Template",
                "url": doc_url,
                "fileType": "docx",
                "key": doc_key,
                "permissions": {
                    "edit": True,
                    "download": True,
                    "print": True,
                    "review": True
                }
            },
            "documentType": "word",
            "editorConfig": {
                "mode": "edit",
                "callbackUrl": callback_url,
                "user": {
                    "id": "admin",
                    "name": "Admin User"
                },
                "customization": {
                    "autosave": True,
                    "forcesave": True
                }
            }
        }
        
        # Generate JWT token if secret is configured
        token = None
        if ONLYOFFICE_JWT_SECRET:
            token = generate_jwt_token(config)
            current_app.logger.info("JWT token generated for ONLYOFFICE")
        else:
            current_app.logger.warning("ONLYOFFICE_JWT_SECRET not set - running without JWT")
        
        current_app.logger.info(f"ONLYOFFICE Document Key: {doc_key}")
        current_app.logger.info(f"Document URL (ONLYOFFICE): {doc_url}")
        current_app.logger.info(f"Document URL (browser test): {doc_url_for_browser if not use_direct_url else doc_url_direct}")
        current_app.logger.info(f"Callback URL: {callback_url}")
        
        # Render the ONLYOFFICE editor page
        return render_template(
            "admin_edit_template.html",
            doc_title="CLP Template",
            doc_url=doc_url,
            doc_url_test=doc_url_direct if use_direct_url else doc_url_for_browser,
            callback_url=callback_url,
            doc_key=doc_key,
            token=token,
            config=config,
            use_direct_url=use_direct_url
        )

    except Exception as e:
        current_app.logger.error(f"Error initializing ONLYOFFICE editor: {e}")
        flash(f"Error initializing editor: {str(e)}", "danger")
        return redirect(url_for("main.dashboard"))


@admin_bp.route('/onlyoffice_callback', methods=['POST'])
def onlyoffice_callback():
    """Handle ONLYOFFICE save callback — upload updated DOCX to Supabase
    
    NOTE: This endpoint must NOT have @login_required because ONLYOFFICE 
    calls it directly without authentication.
    """
    try:
        data = request.get_json()
        current_app.logger.info(f"ONLYOFFICE callback received: {data}")
        
        status = data.get("status")
        
        # Status codes:
        # 0 - no document with the key identifier could be found
        # 1 - document is being edited
        # 2 - document is ready for saving
        # 3 - document saving error has occurred
        # 4 - document is closed with no changes
        # 6 - document is being edited, but the current document state is saved
        # 7 - error has occurred while force saving the document
        
        if status == 2 or status == 6:  # Document ready for saving or force save
            download_url = data.get("url")
            
            if not download_url:
                current_app.logger.error("No download URL in callback")
                return jsonify({"error": 1})
            
            current_app.logger.info(f"Downloading document from: {download_url}")
            
            # Download updated file from ONLYOFFICE
            response = requests.get(download_url, timeout=30)
            response.raise_for_status()
            file_data = response.content
            
            current_app.logger.info(f"Downloaded {len(file_data)} bytes")
            
            # Upload to Supabase
            result = supabase.storage.from_(STORAGE_BUCKET_NAME).update(
                path=TEMPLATE_KEY,
                file=file_data,
                file_options={"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "upsert": "true"}
            )
            
            current_app.logger.info(f"✅ Template updated in Supabase: {result}")
            return jsonify({"error": 0})
        
        elif status == 1 or status == 4:
            # Document is being edited or closed with no changes
            current_app.logger.info(f"Status {status}: Document being edited or closed with no changes")
            return jsonify({"error": 0})
        
        else:
            # Error status
            current_app.logger.error(f"ONLYOFFICE error status: {status}")
            return jsonify({"error": 1})
            
    except Exception as e:
        current_app.logger.error(f"Error in ONLYOFFICE callback: {e}")
        import traceback
        current_app.logger.error(traceback.format_exc())
        return jsonify({"error": 1})


@admin_bp.route('/download_template')
@login_required
@admin_required
def download_template():
    """Download the current template file"""
    try:
        template_bytes = supabase.storage.from_(STORAGE_BUCKET_NAME).download(TEMPLATE_KEY)
        
        if not template_bytes:
            flash("Template file not found.", "danger")
            return redirect(url_for('admin.manage_template'))
        
        return Response(
            template_bytes,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            headers={"Content-Disposition": f"attachment; filename=CLP_Template.docx"}
        )
    except Exception as e:
        flash(f"Error downloading template: {str(e)}", "danger")
        return redirect(url_for('admin.manage_template'))


@admin_bp.route('/upload_template', methods=['POST'])
@login_required
@admin_required
def upload_template():
    """Upload a new template file to replace the current one"""
    if 'file' not in request.files:
        flash("No file uploaded.", "danger")
        return redirect(url_for('admin.manage_template'))
    
    file = request.files['file']
    
    if file.filename == '':
        flash("No file selected.", "danger")
        return redirect(url_for('admin.manage_template'))
    
    if not file.filename.endswith('.docx'):
        flash("Only .docx files are allowed.", "danger")
        return redirect(url_for('admin.manage_template'))
    
    try:
        file_content = file.read()
        
        supabase.storage.from_(STORAGE_BUCKET_NAME).update(
            path=TEMPLATE_KEY,
            file=file_content,
            file_options={"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
        )
        
        flash("New template uploaded successfully!", "success")
    except Exception as e:
        current_app.logger.error(f"Error uploading template: {e}")
        flash(f"Error uploading template: {str(e)}", "danger")
    
    return redirect(url_for('admin.manage_template'))


@admin_bp.route('/dashboard')
@login_required
@roles_required('admin')
def admin_dashboard():
    pending_users_res = supabase.table('users').select('*').eq('approved', False).execute()
    approved_users_res = supabase.table('users').select('*').eq('approved', True).neq('role', 'admin').execute()
    
    pending_user_forms = []
    for user in pending_users_res.data:
        form = ApproveUserForm(user_id=user['id'])
        form.assigned_department.data = user.get('assigned_department')
        form.role.data = user.get('role', 'teacher')
        form.title_display = user.get('title')
        pending_user_forms.append({'user': user, 'form': form})
        
    return render_template('admin_dashboard.html',
                           pending_user_forms=pending_user_forms,
                           approved_users=approved_users_res.data)


@admin_bp.route('/approve_user', methods=['POST'])
@login_required
@roles_required('admin')
def approve_user_action():
    form = ApproveUserForm()
    if form.validate_on_submit():
        user_id = form.user_id.data
        update_data = {
            'approved': True,
            'role': form.role.data,
            'assigned_department': form.assigned_department.data if form.assigned_department.data else None
        }
        try:
            user_res = supabase.table('users').update(update_data).eq('id', user_id).execute()
            if user_res.data:
                flash(f"User {user_res.data[0]['username']} has been approved and assigned.", 'success')
        except PostgrestAPIError as e:
            flash(f"Database error: {e.message}", 'danger')
    else:
        flash("Form validation failed. Could not approve user.", "danger")
    return redirect(url_for('admin.admin_dashboard'))


@admin_bp.route('/disapprove/<user_id>', methods=['POST'])
@login_required
@roles_required('admin')
def disapprove_user(user_id):
    try:
        user_res = supabase.table('users').delete().eq('id', user_id).execute()
        if user_res.data:
            flash(f"User profile for {user_res.data[0]['username']} has been disapproved and removed.", 'warning')
        else:
            flash("User profile not found or already removed.", "info")
    except PostgrestAPIError as e:
        flash(f"Database error: {e.message}", 'danger')
    return redirect(url_for('admin.admin_dashboard'))