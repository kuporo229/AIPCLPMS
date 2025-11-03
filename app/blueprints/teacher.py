# app/blueprints/teacher.py

import os
import json
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   session, abort, send_file, jsonify, Response, current_app)
from supabase import PostgrestAPIError
from app import supabase, STORAGE_BUCKET_NAME
from app.forms import (CLPUploadForm, CLPGenerateForm, CLPUpdateForm,
                       ChangePasswordForm)
from app.decorators import login_required, roles_required
from app.utils import (allowed_file, get_current_user_profile,
                       parse_supabase_timestamp, start_clp_generation)
from app.utils import create_notification
from app.utils import secure_filename, datetime
# Make sure to import it if it's not already there
from app.utils import (allowed_file, get_current_user_profile,
                       parse_supabase_timestamp, start_clp_generation, create_notification,generate_clp_background_task,)
# Create a Blueprint for teacher routes
from app.forms import CLPUploadForm, GenerateAIForm 
from app.forms import (CLPUploadForm, CLPGenerateForm, CLPUpdateForm,
                       ChangePasswordForm, GenerateAIForm) 
# Add these imports at the top of teacher.py
import jwt
import time
import requests
import hashlib
import platform # Added for host detection
import traceback # Added for detailed error logging in callback
from app.utils import generate_jwt_token # Import the function from utils
teacher_bp = Blueprint('teacher', __name__)

@teacher_bp.route('/clp/<int:plan_id>/edit_document')
@login_required
@roles_required('teacher')
def edit_clp_document(plan_id):
    plan_res = supabase.table('course_learning_plans').select('*').eq('id', plan_id).single().execute()
    plan = plan_res.data

    if not plan: abort(404)
    if plan['user_id'] != session['user_id']: abort(403)
    if plan['upload_type'] != 'file_upload' or not plan['filename'] or not plan['filename'].lower().endswith('.docx'):
        flash('This plan is not a .docx document and cannot be edited with the document editor.', 'warning')
        return redirect(url_for('teacher.view_clp', plan_id=plan_id))
    # Optional: Check status if editing should be disallowed for pending/approved
    # if plan['status'] in ['pending', 'approved']:
    #     flash(f'This plan is currently "{plan["status"]}" and cannot be edited.', 'warning')
    #     return redirect(url_for('teacher.teacher_my_clps'))

    try:
        # --- Key Generation ---
        # Create a stable key based on plan ID and filename (or last modified time if available)
        key_string = f"clp_{plan_id}_{plan['filename']}"
        doc_key = hashlib.md5(key_string.encode()).hexdigest()

        # --- Document URL ---
        supabase_url = current_app.config.get("SUPABASE_URL")
        storage_bucket = current_app.config.get("STORAGE_BUCKET_NAME")
        # Assuming files require authentication (using proxy) or are public
        # Option 1: Direct Public URL (Requires bucket/file to be public)
        # doc_url_direct = f"{supabase_url}/storage/v1/object/public/{storage_bucket}/{plan['filename']}"

        # Option 2: Flask Proxy URL (More secure if files aren't public)
        # Determine host URL for ONLYOFFICE (from inside Docker or same network)
        if platform.system() == "Windows":
      # Use Docker Desktop's special DNS name for the host
            onlyoffice_host = "http://host.docker.internal:5000"
        elif platform.system() == "Darwin": # macOS
            onlyoffice_host = "http://172.17.0.1:5000" # Also uses this often
        else: # Linux - Default gateway IP for Docker bridge network
      # This might need adjustment based on your specific Docker network setup
            onlyoffice_host = "http://172.17.0.1:5000" # Common Docker bridge network IP

        # Use the host from the browser request if not running in Docker or for testing
        browser_host = request.host_url.rstrip('/')
        if 'localhost' in browser_host or '127.0.0.1' in browser_host:
             effective_host = browser_host # Use browser host if Flask is run locally
        else:
             effective_host = onlyoffice_host # Assume ONLYOFFICE needs the Docker host

        # URL for ONLYOFFICE to fetch the document via Flask
        # We need a new proxy route: /serve_clp/<plan_id>/<doc_key>
        doc_url_proxy = f"{effective_host}/teacher/serve_clp/{plan_id}/{doc_key}"
        # URL for browser testing the proxy route
        doc_url_for_browser = f"{browser_host}/teacher/serve_clp/{plan_id}/{doc_key}"

        # *** CHOOSE URL METHOD ***
        # Set to True if Supabase bucket/files are public, False to use Flask proxy
        use_direct_url = True
        if use_direct_url:
            supabase_url = current_app.config.get("SUPABASE_URL")
            storage_bucket = current_app.config.get("STORAGE_BUCKET_NAME")
            # Construct the public URL using the plan's filename
            doc_url = f"{supabase_url}/storage/v1/object/public/{storage_bucket}/{plan['filename']}"
            doc_url_test = doc_url # Test URL is the same
            current_app.logger.info(f"Using direct Supabase public URL for CLP {plan_id}: {doc_url}")
            # Determine host for callback separately (still needed)
            ngrok_url = os.getenv("NGROK_URL", "")
            if not ngrok_url:
                # If no ngrok, callback still needs host accessible by ONLYOFFICE
                if platform.system() == "Windows":
                    effective_host_for_callback = "http://host.docker.internal:5000"
                elif platform.system() == "Darwin": # macOS
                    effective_host_for_callback = "http://host.docker.internal:5000"
                else: # Linux
                    effective_host_for_callback = "http://172.17.0.1:5000" # Or appropriate Docker IP
            else:
                effective_host_for_callback = ngrok_url # Ngrok handles callback accessibility
        else:
            # *** Use the determined onlyoffice_host for the proxy URL ***
            doc_url = f"{onlyoffice_host}/teacher/serve_clp/{plan_id}/{doc_key}"
            # Use the browser_host for the test URL the user's browser can access
            doc_url_test = f"{browser_host}/teacher/serve_clp/{plan_id}/{doc_key}"
            current_app.logger.info(f"Using Flask proxy URL for CLP {plan_id}. Editor Fetch URL: {doc_url}")
            # Callback needs the same host as the proxy fetch URL
            effective_host_for_callback = onlyoffice_host


        # --- Callback URL ---
        ngrok_url = os.getenv("NGROK_URL", "") # Check for ngrok URL first
        if ngrok_url:
            callback_url = f"{ngrok_url}/teacher/onlyoffice_clp_callback/{plan_id}/{doc_key}" # Include plan_id/key for identification
            current_app.logger.info(f"Using ngrok callback URL: {callback_url}")
        else:
            callback_url = f"{effective_host}/teacher/onlyoffice_clp_callback/{plan_id}/{doc_key}"
            current_app.logger.info(f"Using Docker/Local callback URL: {callback_url}")


        # --- Editor Configuration ---
        config = {
            "document": {
                "title": os.path.basename(plan['filename']).split('_', 1)[-1], # Cleaner title
                "url": doc_url,
                "fileType": "docx",
                "key": doc_key,
                "permissions": {
                    "edit": True,
                    "download": True, # Allow download from editor
                    "print": True,
                    "review": True
                }
            },
            "documentType": "word",
            "editorConfig": {
                "mode": "edit",
                "callbackUrl": callback_url,
                "user": {
                    # Use actual user ID and maybe username
                    "id": str(session['user_id']),
                    "name": session.get('username', 'Teacher')
                },
                "customization": {
                    "autosave": True,
                    "forcesave": True,
                    "compactToolbar": False,
                    # Add other customizations if needed
                }
            },
             "width": "100%",
             "height": "100%"
        }

        # Generate JWT token
        token = generate_jwt_token(config) # Use the function from utils

        current_app.logger.info(f"Rendering ONLYOFFICE for CLP ID: {plan_id}, Key: {doc_key}")
        current_app.logger.info(f"Document URL (for Editor): {doc_url}")
        current_app.logger.info(f"Document URL (for Browser Test): {doc_url_test}")
        current_app.logger.info(f"Callback URL: {callback_url}")

        return render_template(
            "teacher_edit_document.html", # New template needed
            doc_title=config["document"]["title"],
            doc_url=doc_url,
            doc_url_test=doc_url_test, # Pass the browser-testable URL
            callback_url=callback_url,
            doc_key=doc_key,
            token=token,
            # Pass the whole config if needed by template, otherwise pass specific parts
            # config=json.dumps(config) # If passing the whole config
        )

    except Exception as e:
        current_app.logger.error(f"Error initializing ONLYOFFICE editor for CLP {plan_id}: {e}", exc_info=True)
        flash(f"Error loading document editor: {str(e)}", "danger")
        return redirect(url_for('teacher.view_clp', plan_id=plan_id))
    
@teacher_bp.route('/serve_clp/<int:plan_id>/<doc_key>')
# No login required - ONLYOFFICE fetches this directly
def serve_clp_document(plan_id, doc_key):
    """Serve a specific CLP document to ONLYOFFICE."""
    try:
        # Fetch plan details to get filename and verify ownership (optional but good)
        # Using service client might be needed if RLS prevents direct access here
        # supabase_service = current_app.config['SUPABASE_SERVICE']
        # plan_res = supabase_service.table('course_learning_plans').select('filename, user_id').eq('id', plan_id).single().execute()
        # For simplicity now, directly fetch using user client assuming RLS allows owned reads
        plan_res = supabase.table('course_learning_plans').select('filename, user_id').eq('id', plan_id).single().execute()
        plan = plan_res.data

        if not plan or not plan.get('filename'):
            current_app.logger.error(f"CLP {plan_id} or filename not found for serving.")
            abort(404, description="Document not found")

        # Optional: Verify doc_key consistency (e.g., re-generate and compare)
        # key_string = f"clp_{plan_id}_{plan['filename']}"
        # expected_key = hashlib.md5(key_string.encode()).hexdigest()
        # if doc_key != expected_key:
        #     current_app.logger.error(f"Invalid doc_key provided for CLP {plan_id}.")
        #     abort(403, description="Invalid document key")

        current_app.logger.info(f"Serving CLP {plan_id} (file: {plan['filename']}) with key: {doc_key}")

        # Download from Supabase
        file_bytes = supabase.storage.from_(STORAGE_BUCKET_NAME).download(plan['filename'])

        if not file_bytes:
            current_app.logger.error(f"File {plan['filename']} not found in Supabase storage for CLP {plan_id}")
            abort(404, description="Document file not found in storage")

        current_app.logger.info(f"Successfully downloaded CLP {plan_id}, size: {len(file_bytes)} bytes")

        # Use original filename for download hint
        download_filename = os.path.basename(plan['filename']).split('_', 1)[-1]

        return Response(
            file_bytes,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            headers={
                # Use original filename here
                "Content-Disposition": f"inline; filename=\"{download_filename}\"",
                "Content-Length": str(len(file_bytes)),
                "Accept-Ranges": "bytes", # Important for ONLYOFFICE
                "Cache-Control": "no-cache" # Prevent caching issues
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error serving CLP {plan_id}: {e}", exc_info=True)
        abort(500, description=f"Error serving document: {str(e)}")
    
@teacher_bp.route('/onlyoffice_clp_callback/<int:plan_id>/<doc_key>', methods=['POST'])
# No login required - ONLYOFFICE calls this directly
def onlyoffice_clp_callback(plan_id, doc_key):
    """Handle ONLYOFFICE save callback for teacher CLPs."""
    try:
        data = request.get_json()
        if not data:
            current_app.logger.error(f"Callback for CLP {plan_id} received no JSON data.")
            return jsonify({"error": 1, "message": "No JSON data received"})

        current_app.logger.info(f"ONLYOFFICE callback received for CLP {plan_id}, Key: {doc_key}, Data: {data}")

        status = data.get("status")

        # Verify the key from the callback matches the one in the URL
        if data.get("key") != doc_key:
             current_app.logger.error(f"Callback key mismatch for CLP {plan_id}. Expected {doc_key}, got {data.get('key')}")
             return jsonify({"error": 1, "message": "Key mismatch"})

        # Status 2 (ready for saving) or 6 (force save) means we need to update
        if status in [2, 6]:
            download_url = data.get("url")
            if not download_url:
                current_app.logger.error(f"No download URL in callback for CLP {plan_id}, Status: {status}")
                return jsonify({"error": 1, "message": "Missing download URL"})

            current_app.logger.info(f"Downloading updated document from ONLYOFFICE for CLP {plan_id}: {download_url}")

            # Download updated file from ONLYOFFICE
            response = requests.get(download_url, timeout=60) # Increased timeout
            response.raise_for_status() # Raise exception for bad status codes
            file_data = response.content

            if not file_data:
                 current_app.logger.error(f"Downloaded empty file from ONLYOFFICE for CLP {plan_id}")
                 return jsonify({"error": 1, "message": "Downloaded empty file"})

            current_app.logger.info(f"Downloaded {len(file_data)} bytes for CLP {plan_id}")

            # Fetch the existing filename from the database
            plan_res = supabase.table('course_learning_plans').select('filename, user_id').eq('id', plan_id).single().execute()
            plan = plan_res.data
            if not plan or not plan.get('filename'):
                current_app.logger.error(f"Could not retrieve filename from database for CLP {plan_id} during callback.")
                return jsonify({"error": 1, "message": "Could not find original plan or filename"})

            # Use the existing filename path to update the file in Supabase
            storage_path = plan['filename']
            current_app.logger.info(f"Uploading updated file to Supabase Storage at path: {storage_path} for CLP {plan_id}")

            result = supabase.storage.from_(STORAGE_BUCKET_NAME).update(
                path=storage_path,
                file=file_data,
                file_options={"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "upsert": "true"} # Use upsert true
            )

            current_app.logger.info(f"✅ Successfully updated CLP {plan_id} in Supabase Storage. Result: {result}")

            # Optional: Update a 'last_edited' timestamp in the database table
            # supabase.table('course_learning_plans').update({'last_edited': datetime.utcnow().isoformat()}).eq('id', plan_id).execute()

            return jsonify({"error": 0}) # IMPORTANT: Return error 0 on success

        elif status in [1, 4, 7]: # 1: editing, 4: closed no changes, 7: force save error
            current_app.logger.info(f"Callback for CLP {plan_id}: Status {status} - No action needed.")
            return jsonify({"error": 0})
        else: # 0: key not found, 3: saving error
            current_app.logger.error(f"Callback for CLP {plan_id}: Received error status {status}. Data: {data}")
            return jsonify({"error": 1, "message": f"Received error status {status} from ONLYOFFICE"})

    except requests.exceptions.RequestException as e:
         current_app.logger.error(f"Network error during ONLYOFFICE callback for CLP {plan_id}: {e}", exc_info=True)
         return jsonify({"error": 1, "message": f"Network error: {str(e)}"})
    except PostgrestAPIError as e:
        current_app.logger.error(f"Supabase API error during ONLYOFFICE callback for CLP {plan_id}: {e}", exc_info=True)
        return jsonify({"error": 1, "message": f"Database error: {e.message}"})
    except Exception as e:
        current_app.logger.error(f"Unexpected error in ONLYOFFICE callback for CLP {plan_id}: {e}", exc_info=True)
        # Log detailed traceback
        current_app.logger.error(traceback.format_exc())
        return jsonify({"error": 1, "message": f"Internal server error: {str(e)}"})
    

@teacher_bp.route('/create_clp', methods=['GET', 'POST'])
@login_required
@roles_required(['teacher'])
def create_clp():
    upload_form = CLPUploadForm()

    # Fetch departments for the form if needed (assuming departments exist in Supabase)
    departments = []
    try:
        response = supabase.table('departments').select('id, name').execute()
        departments = response.data
    except Exception as e:
        flash(f"Error fetching departments: {e}", "error")

    # If the file upload form is submitted
    if upload_form.validate_on_submit() and upload_form.upload_file.data:
        file = upload_form.upload_file.data
        if file:
            filename = file.filename
            user_id = session['user_id']
            try:
                # Assuming upload_file_to_supabase returns the URL
                file_url = upload_file_to_supabase(file, f"clp_documents/{user_id}/{filename}")
                
                # Insert CLP record into database
                supabase.table('course_learning_plans').insert({
                    "teacher_id": user_id,
                    "file_url": file_url,
                    "file_name": filename,
                    "status": "pending_dean_review",
                    "generated_by_ai": False,
                    "department_id": upload_form.department.data # Associate with department
                }).execute()

                flash("CLP uploaded successfully and pending dean review.", "success")
                return redirect(url_for('teacher.index'))
            except PostgrestAPIError as e:
                flash(f"Database error: {e.message}", "error")
            except Exception as e:
                flash(f"Error uploading file: {e}", "error")
    
    # Render the page with the upload form and a link/button to the AI generation page
    return render_template('teacher/create_clp.html', upload_form=upload_form, departments=departments)


@teacher_bp.route('/create_clp_ai', methods=['GET', 'POST'])
@login_required
@roles_required('teacher')
def create_clp_ai():
    form = GenerateAIForm()
    user_id = session.get('user_id')

    # Fetch departments for the form (same as create_clp)
    
    if form.validate_on_submit():
        if user_id:
            try:
                # 1. Collect ALL data from the form
                course_data = {
                    "department": dict(form.department.choices).get(form.department.data, form.department.data),
                    "subject": form.course_title.data, # Using title as the main subject key
                    
                    # Collecting all other fields directly from the form
                    "course_number": form.course_code.data,
                    "course_title": form.course_title.data,
                    "descriptive_title": form.course_title.data,
                    "type_of_course": form.type_of_course.data,
                    "units": str(form.unit.data), # Ensure it's a string to match AI output format
                    "pre_requisite": form.pre_requisite.data,
                    "co_requisite": form.co_requisite.data,
                    "credit": str(form.credit.data), # Ensure it's a string
                    "Contact_hours_per_week": form.contact_hours_per_week.data,
                    "class_schedule": form.class_schedule.data,
                    "room_assignment": form.room_assignment.data,
                    # Note: course_description is not passed to the AI prompt, but we should save it if needed.
                }

                # --- FIX 1: Pass the entire course_data dictionary to the background task ---
                # We need to update the signature of start_clp_generation and generate_clp_background_task
                start_clp_generation(user_id, course_data) 
                
                flash("AI CLP generation started. You will be notified when it's ready.", "info")
                return redirect(url_for('teacher.teacher_my_clps')) # Redirect to my_clps, the new dashboard after AI generation
            except Exception as e:
                # This catches errors that occur when starting the thread
                flash(f"Error initiating AI generation: {e}", "danger")
        else:
            flash("User not logged in.", "danger")
            return redirect(url_for('auth.login'))
    
    return render_template('create_clp_ai.html', form=form)

@teacher_bp.route('/my_clps')
@login_required
@roles_required('teacher')
def teacher_my_clps():
    upload_form = CLPUploadForm()
    generate_form = CLPGenerateForm()
    
    # Corrected Supabase query: Select the plan data AND the related author's username
    plans_res = supabase.table('course_learning_plans').select('*, author:users(id, username)').eq('user_id', session['user_id']).order('date_posted', desc=True).execute()
    count_res = supabase.table('notifications').select('id', count='exact').eq('user_id', session['user_id']).eq('is_read', False).execute()
    
    # Call the helper function to format the plan dates before rendering
    plans_data = parse_supabase_timestamp(plans_res.data, 'date_posted')
    
    return render_template('teacher_courses.html', 
                           title='My Courses', 
                           upload_form=upload_form, 
                           generate_form=generate_form, 
                           plans=plans_data, 
                           unread_notifications=count_res.count)

@teacher_bp.route('/all_clps')
@login_required
@roles_required('teacher', 'dean')
def teacher_all_clps():
    # Shows all *approved* plans from all users
    plans_res = supabase.table('course_learning_plans').select('*, author:users(username)').eq('status', 'approved').order('date_posted', desc=True).execute()
    count_res = supabase.table('notifications').select('id', count='exact').eq('user_id', session['user_id']).eq('is_read', False).execute()

    # --- FIX: Convert timestamp strings to datetime objects for the template ---
    plans_data = parse_supabase_timestamp(plans_res.data, 'date_posted')

    return render_template('all_courses.html', title='All Approved Course Learning Plans', plans=plans_data, unread_notifications=count_res.count)

@teacher_bp.route('/submit_to_dean/<int:plan_id>', methods=['POST'])
@login_required
@roles_required('teacher')
def submit_to_dean(plan_id):
    plan_res = supabase.table('course_learning_plans').select('*, author:users(id, username)').eq('id', plan_id).single().execute()
    plan = plan_res.data

    if not plan: abort(404)
    if plan['author']['id'] != session['user_id']: abort(403)
    
    if plan['status'] in ['pending', 'approved']:
        flash('This plan is already pending or has been approved.', 'info')
    else:
        try:
            supabase.table('course_learning_plans').update({
                'status': 'pending',
                'dean_comments': None
            }).eq('id', plan_id).execute()
            
            deans_res = supabase.table('users').select('id').eq('role', 'dean').eq('approved', True).execute()
            for dean in deans_res.data:
                create_notification(dean['id'], f'New CLP for "{plan["subject"]}" from {plan["author"]["username"]} needs review.')
            flash(f'CLP for "{plan["subject"]}" submitted to Dean for review.', 'success')
        except PostgrestAPIError as e:
            flash(f"Error submitting plan: {e.message}", 'danger')
            
    return redirect(url_for('teacher.teacher_my_clps'))


@teacher_bp.route('/courses/upload', methods=['POST'])
@login_required
@roles_required('teacher')
def upload_clp():
    form = CLPUploadForm()
    if form.validate_on_submit():
        user_id = session['user_id']
        file = form.file.data
        
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            # Create a unique path for the file in the bucket to avoid collisions
            file_path_in_bucket = f"{user_id}/{datetime.utcnow().timestamp()}_{filename}"
            
            try:
                supabase.storage.from_(STORAGE_BUCKET_NAME).upload(
                    file=file.read(), 
                    path=file_path_in_bucket, 
                    file_options={"content-type": file.mimetype}
                )
                supabase.table('course_learning_plans').insert({
                    'department': form.department.data, 'subject': form.subject.data,
                    'filename': file_path_in_bucket, 'upload_type': 'file_upload',
                    'status': 'draft', 'user_id': user_id
                }).execute()
                flash('Your CLP file has been uploaded as a draft!', 'success')
            except Exception as e:
                flash(f"An error occurred during file upload: {e}", 'danger')
        
        elif form.content.data:
            supabase.table('course_learning_plans').insert({
                'department': form.department.data, 'subject': form.subject.data,
                'content': form.content.data, 'upload_type': 'manual_text',
                'status': 'draft', 'user_id': user_id
            }).execute()
            flash('Your CLP content has been saved as a draft!', 'success')
        else:
            flash('Please provide either content or upload a file.', 'danger')
    else:
        # Flash form errors
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"Error in {field.replace('_', ' ').title()}: {error}", 'danger')
    return redirect(url_for('teacher.teacher_my_clps'))

@teacher_bp.route('/clp/<int:plan_id>/delete_approved', methods=['POST'])
@login_required
@roles_required('teacher', 'dean')
def delete_approved_clp(plan_id):
    plan_res = supabase.table('course_learning_plans').select('user_id, filename, subject').eq('id', plan_id).single().execute()
    plan = plan_res.data
    
    if not plan: abort(404)
    
    # Security check: A teacher can only delete their own approved plans. A dean can delete any approved plan.
    if session['role'] == 'teacher' and plan['user_id'] != session['user_id']:
        flash('You do not have permission to delete this plan.', 'danger')
        return redirect(url_for('teacher.teacher_all_clps'))
    
    # If the plan has an associated file, delete it from Supabase Storage
    if plan.get('filename'):
        try:
            supabase.storage.from_(STORAGE_BUCKET_NAME).remove([plan['filename']])
            flash(f"Associated file for '{plan['subject']}' deleted from storage.", 'info')
        except Exception as e:
            flash(f"Error deleting file from storage: {e}", 'danger')
            
    # Delete the plan from the database
    try:
        supabase.table('course_learning_plans').delete().eq('id', plan_id).execute()
        flash(f"Approved Course Learning Plan for '{plan['subject']}' has been deleted.", 'success')
    except PostgrestAPIError as e:
        flash(f"Database error during deletion: {e.message}", 'danger')

    if session['role'] == 'dean':
        return redirect(url_for('dean.dean_courses'))
    else:
        return redirect(url_for('teacher.teacher_my_clps'))


@teacher_bp.route('/courses/generate', methods=['POST'])
@login_required
@roles_required('teacher')
def generate_clp():
    form = CLPGenerateForm()
    if form.validate_on_submit():
        GEMINI_API_KEY = current_app.config.get('GEMINI_API_KEY')
        if not GEMINI_API_KEY:
            flash('AI service is not configured. Please contact an administrator.', 'danger')
            return redirect(url_for('teacher.teacher_my_clps'))
        try:
            # Start the background task using the new helper function
            start_clp_generation(form.subject_name.data, form.department.data, session['user_id'])
            flash('CLP generation started. This may take a moment. You will be notified when it is complete.', 'info')
        except Exception as e:
            flash(f'Failed to start AI generation: {e}.', 'danger')
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"Error in {field.replace('_', ' ').title()}: {error}", 'danger')
    return redirect(url_for('teacher.teacher_my_clps'))

@teacher_bp.route('/clp/<int:plan_id>')
@login_required
@roles_required('teacher', 'dean')
def view_clp(plan_id):
    plan_res = supabase.table('course_learning_plans').select('*, author:users(*)').eq('id', plan_id).single().execute()
    plan = plan_res.data

    if not plan: abort(404)

    # --- FIX: Convert the single plan's timestamp string to a datetime object ---
    parsed_plan_list = parse_supabase_timestamp([plan], 'date_posted')
    plan = parsed_plan_list[0]

    # Check if the user is authorized to view this plan
    if session['role'] == 'teacher' and plan['user_id'] != session['user_id']:
        abort(403)

    if plan['upload_type'] == 'file_upload':
        return render_template('view_uploaded_clp.html', plan=plan)

    try:
        content_data = json.loads(plan.get('content', '{}'))
        return render_template('view_ai_clp.html',
                               plan=plan, content_data=content_data)
    except (json.JSONDecodeError, TypeError):
        # Fallback for plain text content
        return render_template('view_clp.html', plan=plan)

@teacher_bp.route('/clp/<int:plan_id>/download')
@login_required
@roles_required('teacher', 'dean')
def download_clp(plan_id):
    plan_res = supabase.table('course_learning_plans').select('filename, upload_type').eq('id', plan_id).single().execute()
    plan = plan_res.data

    if not plan or plan['upload_type'] != 'file_upload' or not plan['filename']:
        flash('This plan does not have a downloadable file.', 'danger')
        return redirect(url_for('teacher.view_clp', plan_id=plan_id))
    
    try:
        file_bytes = supabase.storage.from_(STORAGE_BUCKET_NAME).download(plan['filename'])
        # Extract original filename for the user by splitting on the timestamp
        download_name = os.path.basename(plan['filename']).split('_', 1)[-1]
        
        return Response(
            file_bytes,
            mimetype='application/octet-stream',
            headers={"Content-disposition": f"attachment; filename=\"{download_name}\""}
        )
    except Exception as e:
        flash(f'Error downloading file: {e}. It may have been deleted from storage.', 'danger')
        return redirect(url_for('teacher.view_clp', plan_id=plan_id))

@teacher_bp.route('/clp/<int:plan_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('teacher')
def edit_clp(plan_id):
    plan_res = supabase.table('course_learning_plans').select('*').eq('id', plan_id).single().execute()
    plan = plan_res.data

    if not plan: abort(404)
    if plan['user_id'] != session['user_id']: abort(403)
    
    if plan['status'] in ['pending', 'approved']:
        flash(f'This plan is currently "{plan["status"]}" and cannot be edited.', 'warning')
        return redirect(url_for('teacher.teacher_my_clps'))
        
    form = CLPUpdateForm()
    if form.validate_on_submit():
        update_data = {'department': form.department.data, 'subject': form.subject.data}
        if form.file.data:
            # Delete old file from storage if it exists
            if plan.get('filename'):
                try: supabase.storage.from_(STORAGE_BUCKET_NAME).remove([plan['filename']])
                except Exception: pass
            
            new_filename = secure_filename(form.file.data.filename)
            file_path = f"{session['user_id']}/{datetime.utcnow().timestamp()}_{new_filename}"
            supabase.storage.from_(STORAGE_BUCKET_NAME).upload(file=form.file.data.read(), path=file_path, file_options={"content-type": form.file.data.mimetype})
            update_data.update({'filename': file_path, 'content': None, 'upload_type': 'file_upload'})
        elif form.content.data:
            update_data.update({'content': form.content.data, 'filename': None, 'upload_type': 'manual_text'})
        
        supabase.table('course_learning_plans').update(update_data).eq('id', plan_id).execute()
        flash('Plan updated successfully!', 'success')
        return redirect(url_for('teacher.teacher_my_clps'))
        
    elif request.method == 'GET':
        form.department.data = plan['department']
        form.subject.data = plan['subject']
        if plan['upload_type'] in ['manual_text', 'ai_generated']:
            form.content.data = plan.get('content') or ''
            
    return render_template('edit_clp.html', title='Edit Plan', form=form, plan=plan)

@teacher_bp.route('/clp/<int:plan_id>/delete', methods=['POST'])
@login_required
@roles_required('teacher')
def delete_clp(plan_id):
    plan_res = supabase.table('course_learning_plans').select('*').eq('id', plan_id).eq('user_id', session['user_id']).single().execute()
    plan = plan_res.data

    if not plan: abort(404)
    
    if plan['status'] in ['pending', 'approved']:
        flash(f'Cannot delete a plan that is currently "{plan["status"]}".', 'danger')
        return redirect(url_for('teacher.teacher_my_clps'))
        
    try:
        # If there's a file, delete it from storage first
        if plan.get('filename'):
            supabase.storage.from_(STORAGE_BUCKET_NAME).remove([plan['filename']])
        
        supabase.table('course_learning_plans').delete().eq('id', plan_id).execute()
        flash('Your Course Learning Plan has been deleted.', 'success')
    except Exception as e:
        flash(f"An error occurred while deleting: {e}", 'danger')
        
    return redirect(url_for('teacher.teacher_my_clps'))

@teacher_bp.route('/profile', methods=['GET', 'POST'])
@login_required
@roles_required('teacher')
def teacher_profile():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        try:
            supabase.auth.update_user({"password": form.new_password.data})
            flash('Your password has been changed successfully.', 'success')
            return redirect(url_for('teacher.teacher_profile'))
        except Exception as e:
            flash(f"Error changing password: {e}", 'danger')
            
    return render_template('teacher_profile.html', form=form, user=get_current_user_profile())