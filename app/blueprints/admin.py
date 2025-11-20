from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app, jsonify, Response
from supabase import PostgrestAPIError
from app import supabase
from app.decorators import login_required, roles_required, admin_required
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
from app.forms import ApproveUserForm, TemplateEditForm, EditUserForm, DepartmentForm, TemplateUploadForm, SystemSettingsForm
from app.utils import parse_supabase_timestamp # Add this to imports

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


# ... existing imports ...

# --- NEW: Dynamic Template Editing ---

@admin_bp.route('/templates/edit/<int:template_id>')
@login_required
@roles_required('admin')
def edit_template(template_id):
    """Open a specific template in ONLYOFFICE editor."""
    try:
        # 1. Fetch template details from DB
        res = supabase.table('templates').select('*').eq('id', template_id).single().execute()
        template = res.data
        
        if not template:
            flash("Template not found.", "danger")
            return redirect(url_for('admin.manage_templates'))

        filename = template['filename'] # e.g., "templates/17154..._MyTemplate.docx"
        doc_title = template['name']

        # 2. Generate a stable document key
        # We use ID and filename. If you had a 'last_modified' field, you'd append that too.
        import hashlib
        key_string = f"tmpl_{template['id']}_{filename}"
        doc_key = hashlib.md5(key_string.encode()).hexdigest()

        # 3. Construct Document URL (Direct Supabase Public URL)
        supabase_url = os.getenv("SUPABASE_URL")
        # Ensure this matches your actual bucket name variable
        doc_url = f"{supabase_url}/storage/v1/object/public/{STORAGE_BUCKET_NAME}/{filename}"

        # 4. Construct Callback URL
        # ONLYOFFICE needs to hit this URL to save changes.
        ngrok_url = os.getenv("NGROK_URL", "")
        
        # Determine base host
        if ngrok_url:
            base_url = ngrok_url
        elif platform.system() == "Windows":
            base_url = "http://host.docker.internal:5000"
        else:
            base_url = "http://172.17.0.1:5000" # Standard Docker bridge IP
            
        # IMPORTANT: We pass the template_id in the callback URL
        callback_url = f"{base_url}/admin/onlyoffice_callback/{template_id}"
        
        current_app.logger.info(f"Editing Template ID: {template_id}")
        current_app.logger.info(f"Doc URL: {doc_url}")
        current_app.logger.info(f"Callback URL: {callback_url}")

        # 5. Editor Config
        config = {
            "document": {
                "title": doc_title,
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
                    "autosave": False,
                    "forcesave": True
                }
            }
        }

        # Generate JWT if secret is set
        token = None
        if ONLYOFFICE_JWT_SECRET:
            token = generate_jwt_token(config)

        return render_template(
            "admin_edit_template.html",
            doc_title=doc_title,
            doc_url=doc_url,
            doc_url_test=doc_url,
            callback_url=callback_url,
            doc_key=doc_key,
            token=token,
            config=config
        )

    except Exception as e:
        current_app.logger.error(f"Error loading template editor: {e}")
        flash(f"Error loading editor: {str(e)}", "danger")
        return redirect(url_for('admin.manage_templates'))


@admin_bp.route('/onlyoffice_callback/<int:template_id>', methods=['POST'])
def onlyoffice_callback(template_id):
    """Handle ONLYOFFICE save callback for a SPECIFIC template."""
    try:
        data = request.get_json()
        status = data.get("status")
        
        # Status 2 (Ready for saving) or 6 (Force save)
        if status in [2, 6]:
            download_url = data.get("url")
            if not download_url:
                return jsonify({"error": 1, "message": "No url"})

            # 1. Get filename from DB
            res = supabase.table('templates').select('filename').eq('id', template_id).single().execute()
            if not res.data:
                current_app.logger.error(f"Callback failed: Template {template_id} not found in DB.")
                return jsonify({"error": 1, "message": "Template not found"})
            
            storage_path = res.data['filename']

            # 2. Download new file from ONLYOFFICE
            file_resp = requests.get(download_url, timeout=30)
            file_resp.raise_for_status()
            file_data = file_resp.content

            # 3. Upload to Supabase (Overwrite)
            supabase.storage.from_(STORAGE_BUCKET_NAME).update(
                path=storage_path,
                file=file_data,
                file_options={"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "upsert": "true"}
            )
            
            current_app.logger.info(f"✅ Template {template_id} updated successfully.")
            return jsonify({"error": 0})

        # Status 1 (Editing) or 4 (Closed no changes) -> No error
        return jsonify({"error": 0})

    except Exception as e:
        current_app.logger.error(f"Callback error: {e}")
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

@admin_bp.route('/user/<user_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('admin')
def edit_user(user_id):
    # Fetch the user to edit
    try:
        user_res = supabase.table('users').select('*').eq('id', user_id).single().execute()
        user = user_res.data
        if not user:
            abort(404)
    except PostgrestAPIError as e:
        flash(f"Database error: {e.message}", "danger")
        return redirect(url_for('admin.admin_dashboard'))

    form = EditUserForm()

    # POST: Update the user
    if form.validate_on_submit():
        update_data = {
            'first_name': form.first_name.data,
            'last_name': form.last_name.data,
            'title': form.title.data,
            'role': form.role.data,
            'assigned_department': form.department.data if form.department.data else None
        }
        try:
            supabase.table('users').update(update_data).eq('id', user_id).execute()
            flash(f"User profile for {user['username']} updated successfully.", "success")
            return redirect(url_for('admin.admin_dashboard'))
        except Exception as e:
            flash(f"Error updating user: {str(e)}", "danger")

    # GET: Pre-fill form with existing data
    if request.method == 'GET':
        form.first_name.data = user.get('first_name')
        form.last_name.data = user.get('last_name')
        form.title.data = user.get('title')
        form.role.data = user.get('role')
        form.department.data = user.get('assigned_department')

    return render_template('admin_edit_user.html', form=form, user=user)

@admin_bp.route('/user/<user_id>/suspend', methods=['POST'])
@login_required
@roles_required('admin')
def suspend_user(user_id):
    """Suspends a user by setting approved=False. They will effectively lose access."""
    try:
        # We simply toggle 'approved' to False. They will move to the 'Pending' list.
        supabase.table('users').update({'approved': False}).eq('id', user_id).execute()
        flash("User suspended. They have been moved to the Pending Approvals list.", "warning")
    except Exception as e:
        flash(f"Error suspending user: {str(e)}", "danger")
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

@admin_bp.route('/departments', methods=['GET', 'POST'])
@login_required
@roles_required('admin')
def manage_departments():
    form = DepartmentForm()
    
    # Handle Adding Department
    if form.validate_on_submit():
        dept_name = form.name.data.strip()
        try:
            # Check for duplicates (simple check)
            existing = supabase.table('departments').select('id').eq('name', dept_name).execute()
            if existing.data:
                flash(f"Department '{dept_name}' already exists.", "warning")
            else:
                supabase.table('departments').insert({'name': dept_name}).execute()
                flash(f"Department '{dept_name}' added successfully!", "success")
                return redirect(url_for('admin.manage_departments'))
        except PostgrestAPIError as e:
            flash(f"Database error: {e.message}", "danger")

    # Fetch Departments for the list
    try:
        dept_res = supabase.table('departments').select('*').order('created_at').execute()
        departments = dept_res.data
    except PostgrestAPIError as e:
        departments = []
        flash(f"Error fetching departments: {e.message}", "danger")

    return render_template('admin_departments.html', form=form, departments=departments)

@admin_bp.route('/departments/delete/<int:dept_id>', methods=['POST'])
@login_required
@roles_required('admin')
def delete_department(dept_id):
    try:
        supabase.table('departments').delete().eq('id', dept_id).execute()
        flash("Department deleted successfully.", "success")
    except PostgrestAPIError as e:
        flash(f"Error deleting department: {e.message}", "danger")
    return redirect(url_for('admin.manage_departments'))
@admin_bp.route('/clps')
@login_required
@roles_required('admin')
def manage_clps():
    # Fetch ALL plans with author details, ordered by newest first
    try:
        plans_res = supabase.table('course_learning_plans').select('*, author:users(username, first_name, last_name)').order('date_posted', desc=True).execute()
        # Format dates
        plans = parse_supabase_timestamp(plans_res.data, 'date_posted')
    except PostgrestAPIError as e:
        flash(f"Database error: {e.message}", "danger")
        plans = []
        
    return render_template('admin_clps.html', plans=plans)

@admin_bp.route('/clp/<int:plan_id>/delete', methods=['POST'])
@login_required
@roles_required('admin')
def delete_clp(plan_id):
    # 1. Fetch the plan to get the filename
    try:
        plan_res = supabase.table('course_learning_plans').select('filename, subject').eq('id', plan_id).single().execute()
        plan = plan_res.data
        
        if not plan:
            flash("Plan not found.", "danger")
            return redirect(url_for('admin.manage_clps'))

        # 2. Delete file from Storage (if it exists)
        if plan.get('filename'):
            try:
                supabase.storage.from_(STORAGE_BUCKET_NAME).remove([plan['filename']])
            except Exception:
                # Continue deleting the record even if file deletion fails (e.g., file already gone)
                pass
        
        # 3. Delete record from Database
        supabase.table('course_learning_plans').delete().eq('id', plan_id).execute()
        flash(f"CLP '{plan['subject']}' has been permanently deleted.", "success")
        
    except Exception as e:
        flash(f"Error deleting plan: {str(e)}", "danger")

    return redirect(url_for('admin.manage_clps'))
@admin_bp.route('/templates', methods=['GET', 'POST'])
@login_required
@roles_required('admin')
def manage_templates():
    form = TemplateUploadForm()
    
    # 1. Handle Upload
    if form.validate_on_submit():
        file = form.file.data
        filename = secure_filename(file.filename)
        # Save to a specific 'templates' folder in storage
        storage_path = f"templates/{int(time.time())}_{filename}"
        
        try:
            # Upload to Supabase Storage
            file_content = file.read()
            supabase.storage.from_(STORAGE_BUCKET_NAME).upload(
                path=storage_path,
                file=file_content,
                file_options={"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
            )

            # Save Metadata to DB
            dept_id = form.department.data if form.department.data else None
            is_def = True if form.is_default.data == 'yes' else False
            
            # If setting as default, unset others
            if is_def:
                supabase.table('templates').update({'is_default': False}).neq('id', 0).execute()

            supabase.table('templates').insert({
                'name': form.name.data,
                'filename': storage_path,
                'department_id': dept_id,
                'is_default': is_def
            }).execute()
            
            flash("Template uploaded successfully!", "success")
            return redirect(url_for('admin.manage_templates'))
            
        except Exception as e:
            flash(f"Error uploading template: {str(e)}", "danger")

    # 2. List Templates
    try:
        # Fetch templates and join with department names
        res = supabase.table('templates').select('*, departments(name)').order('created_at', desc=True).execute()
        templates = res.data
    except Exception as e:
        templates = []
        flash(f"Error loading templates: {str(e)}", "danger")

    return render_template('admin_templates.html', form=form, templates=templates)

@admin_bp.route('/templates/delete/<int:template_id>', methods=['POST'])
@login_required
@roles_required('admin')
def delete_template(template_id):
    try:
        # Get filename to delete from storage
        res = supabase.table('templates').select('filename').eq('id', template_id).single().execute()
        if res.data:
            supabase.storage.from_(STORAGE_BUCKET_NAME).remove([res.data['filename']])
            
        supabase.table('templates').delete().eq('id', template_id).execute()
        flash("Template deleted.", "success")
    except Exception as e:
        flash(f"Error deleting template: {e}", "danger")
    return redirect(url_for('admin.manage_templates'))
@admin_bp.route('/settings', methods=['GET', 'POST'])
@login_required
@roles_required('admin')
def system_settings():
    form = SystemSettingsForm()
    
    if form.validate_on_submit():
        try:
            # Upsert each setting
            updates = [
                {'key': 'prompt_po_io', 'value': form.prompt_po_io.data},
                {'key': 'prompt_co_po', 'value': form.prompt_co_po.data},
                {'key': 'prompt_weekly', 'value': form.prompt_weekly.data}
            ]
            
            for setting in updates:
                supabase.table('system_settings').update({'value': setting['value']}).eq('key', setting['key']).execute()
                
            flash("System settings updated successfully.", "success")
            return redirect(url_for('admin.system_settings'))
            
        except Exception as e:
            flash(f"Error updating settings: {str(e)}", "danger")
    
    # GET request: Populate form from DB
    if request.method == 'GET':
        try:
            res = supabase.table('system_settings').select('*').execute()
            settings_map = {item['key']: item['value'] for item in res.data}
            
            form.prompt_po_io.data = settings_map.get('prompt_po_io', '')
            form.prompt_co_po.data = settings_map.get('prompt_co_po', '')
            form.prompt_weekly.data = settings_map.get('prompt_weekly', '')
        except Exception as e:
            flash(f"Error fetching settings: {str(e)}", "danger")

    return render_template('admin_settings.html', form=form)