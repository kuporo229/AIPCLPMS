# app/blueprints/dean.py

import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, session
from supabase import PostgrestAPIError
from app import supabase, PROGRAM_OUTCOMES, COURSE_OUTCOMES, INSTITUTIONAL_OUTCOMES_HEADERS, PROGRAM_OUTCOMES_HEADERS
from app.forms import DeanReviewForm, ChangePasswordForm
from app.decorators import login_required, roles_required
from app.utils import get_current_user_profile, create_notification
from app.utils import create_notification
from app.utils import get_current_user_profile, create_notification, parse_supabase_timestamp ,current_app
import os
import hashlib
import requests
import platform
import time
import traceback
from flask import jsonify, Response, current_app
from app import STORAGE_BUCKET_NAME
from app.utils import generate_jwt_token
# Create a Blueprint for dean routes
dean_bp = Blueprint('dean', __name__)

@dean_bp.route('/faculty')
@login_required
@roles_required('dean')
def dean_faculty():
    teachers_res = supabase.table('users').select('*').eq('role', 'teacher').order('username').execute()
    return render_template('dean_faculty.html', teachers=teachers_res.data)

@dean_bp.route('/courses')
@login_required
@roles_required('dean')
def dean_courses():
    # Fetching plans with author's username using a join
    pending_plans_res = supabase.table('course_learning_plans').select('*, author:users(username)').eq('status', 'pending').order('date_posted', desc=True).execute()
    approved_plans_res = supabase.table('course_learning_plans').select('*, author:users(username)').eq('status', 'approved').order('date_posted', desc=True).execute()
    count_res = supabase.table('notifications').select('id', count='exact').eq('user_id', session['user_id']).eq('is_read', False).execute()

    # --- FIX: Convert timestamp strings to datetime objects for the template ---
    pending_plans_data = parse_supabase_timestamp(pending_plans_res.data, 'date_posted')
    approved_plans_data = parse_supabase_timestamp(approved_plans_res.data, 'date_posted')

    return render_template('dean_courses.html',
                           pending_plans=pending_plans_data,
                           approved_plans=approved_plans_data,
                           unread_notifications=count_res.count)

@dean_bp.route('/review_clp/<int:plan_id>', methods=['GET', 'POST'])
@login_required
@roles_required('dean')
def dean_review_clp(plan_id):
    plan_res = supabase.table('course_learning_plans').select('*, author:users(*)').eq('id', plan_id).single().execute()
    plan = plan_res.data

    if not plan: abort(404)

    parsed_plan_list = parse_supabase_timestamp([plan], 'date_posted')
    plan = parsed_plan_list[0]

    if plan['status'] != 'pending':
        flash('This plan is not pending review.', 'warning')
        return redirect(url_for('dean.dean_courses'))

    form = DeanReviewForm()
    if form.validate_on_submit():
        comments = form.comments.data
        new_status = ''
        if form.submit_approve.data:
            new_status = 'approved'
            flash(f'CLP for {plan["subject"]} has been approved.', 'success')
            create_notification(plan['user_id'], f'Your CLP for "{plan["subject"]}" has been APPROVED by the Dean.')
        elif form.submit_return.data:
            new_status = 'returned_for_revision'
            flash(f'CLP for {plan["subject"]} has been returned for revision.', 'warning')
            create_notification(plan['user_id'], f'Your CLP for "{plan["subject"]}" has been RETURNED. Comments: {comments or "None"}')

        try:
            print(f"DEBUG: Updating plan {plan_id} with status='{new_status}' and comments='{comments}'")
            update_res = supabase.table('course_learning_plans').update({
            'status': new_status,
            'dean_comments': comments
                }).eq('id', plan_id).execute()
            supabase.table('course_learning_plans').update({
                'status': new_status,
                'dean_comments': comments
            }).eq('id', plan_id).execute()
        except PostgrestAPIError as e:
            flash(f"Error updating plan: {e.message}", 'danger')
        return redirect(url_for('dean.dean_courses'))

    content_data = {}
    if plan['upload_type'] == 'ai_generated' and plan.get('content'):
        try:
            content_data = json.loads(plan['content'])
        except json.JSONDecodeError:
            flash('Error loading AI-generated content.', 'danger')
            content_data = {'descriptive_title': plan.get('subject', 'Error')}

    return render_template('dean_review_clp.html', plan=plan, form=form, content_data=content_data,
                           program_outcomes=current_app.config['PROGRAM_OUTCOMES'], course_outcomes=current_app.config['COURSE_OUTCOMES'],
                           institutional_headers=current_app.config['INSTITUTIONAL_OUTCOMES_HEADERS'], program_headers=current_app.config['PROGRAM_OUTCOMES_HEADERS'])

@dean_bp.route('/profile', methods=['GET', 'POST'])
@login_required
@roles_required('dean')
def dean_profile():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        try:
            # The user must be logged in to update their password this way.
            supabase.auth.update_user({"password": form.new_password.data})
            flash('Your password has been changed successfully.', 'success')
            return redirect(url_for('dean.dean_profile'))
        except Exception as e:
            flash(f"Error changing password: {e}", 'danger')
            
    return render_template('dean_profile.html', form=form, user=get_current_user_profile())
# --- ONLYOFFICE DOCUMENT REVIEW FOR DEAN ---

@dean_bp.route('/clp/<int:plan_id>/review_document')
@login_required
@roles_required('dean')
def review_clp_document(plan_id):
    """Open the CLP in ONLYOFFICE editor for review/editing."""
    try:
        # 1. Fetch plan details
        res = supabase.table('course_learning_plans').select('*, author:users(username)').eq('id', plan_id).single().execute()
        plan = res.data
        
        if not plan: abort(404)
        
        # Check if it's a valid document
        if not plan.get('filename') or not plan['filename'].lower().endswith('.docx'):
            flash('This plan is not a .docx document and cannot be opened in the editor.', 'warning')
            return redirect(url_for('dean.dean_review_clp', plan_id=plan_id))

        # 2. Generate Key
        key_string = f"dean_review_{plan['id']}_{plan['filename']}"
        doc_key = hashlib.md5(key_string.encode()).hexdigest()
        
        doc_title = f"REVIEW: {plan['subject']}"

        # 3. Construct Document URL (FIX: Use Direct Supabase Public URL)
        # This avoids "Download failed" errors caused by Docker networking issues
        supabase_url = os.getenv("SUPABASE_URL")
        doc_url = f"{supabase_url}/storage/v1/object/public/{STORAGE_BUCKET_NAME}/{plan['filename']}"
        
        # Browser test URL (same as doc_url)
        doc_url_test = doc_url
        
        # 4. Construct Callback URL (Keep this pointing to your app)
        # ONLYOFFICE sends changes back to this URL
        ngrok_url = os.getenv("NGROK_URL", "")
        if ngrok_url:
            callback_url = f"{ngrok_url}/dean/onlyoffice_callback/{plan_id}/{doc_key}"
        else:
            # Fallback for local dev if NGROK not set (might fail if ONLYOFFICE can't see host)
            if platform.system() == "Windows":
                base_url = "http://host.docker.internal:5000"
            else:
                base_url = "http://172.17.0.1:5000"
            callback_url = f"{base_url}/dean/onlyoffice_callback/{plan_id}/{doc_key}"

        current_app.logger.info(f"Dean Review - Doc URL: {doc_url}")
        current_app.logger.info(f"Dean Review - Callback URL: {callback_url}")

        # 5. Editor Config
        config = {
            "document": {
                "title": doc_title,
                "url": doc_url,
                "fileType": "docx",
                "key": doc_key,
                "permissions": {
                    "edit": True,      # Dean can edit
                    "download": True,
                    "print": True,
                    "review": True     # Enable Track Changes by default
                }
            },
            "documentType": "word",
            "editorConfig": {
                "mode": "edit",
                "callbackUrl": callback_url,
                "user": {
                    "id": str(session['user_id']),
                    "name": f"Dean {session.get('username', '')}"
                },
                "customization": {
                    "autosave": True,
                    "forcesave": True,
                    "compactToolbar": False
                }
            }
        }

        token = generate_jwt_token(config)

        return render_template(
            "dean_review_document.html",
            doc_title=doc_title,
            doc_url=doc_url,
            doc_url_test=doc_url_test,
            callback_url=callback_url,
            doc_key=doc_key,
            token=token,
            plan_id=plan_id
        )

    except Exception as e:
        current_app.logger.error(f"Error opening Dean editor: {e}")
        flash(f"Error loading editor: {str(e)}", "danger")
        return redirect(url_for('dean.dean_review_clp', plan_id=plan_id))

@dean_bp.route('/serve_clp_doc/<int:plan_id>/<doc_key>')
def serve_clp_doc(plan_id, doc_key):
    """Serve the CLP file content to ONLYOFFICE."""
    try:
        # Fetch plan to get filename
        res = supabase.table('course_learning_plans').select('filename').eq('id', plan_id).single().execute()
        if not res.data:
            abort(404)
            
        filename = res.data['filename']
        
        # Download from Storage
        file_bytes = supabase.storage.from_(STORAGE_BUCKET_NAME).download(filename)
        
        if not file_bytes:
            abort(404)
            
        return Response(
            file_bytes,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            headers={
                "Content-Disposition": f"inline; filename=review.docx",
                "Content-Length": str(len(file_bytes)),
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-cache"
            }
        )
    except Exception as e:
        current_app.logger.error(f"Dean Serve Error: {e}")
        abort(500)


@dean_bp.route('/onlyoffice_callback/<int:plan_id>/<doc_key>', methods=['POST'])
def onlyoffice_callback(plan_id, doc_key):
    """Handle saving changes made by the Dean."""
    try:
        data = request.get_json()
        status = data.get("status")
        
        # Status 2 (Ready for saving) or 6 (Force save)
        if status in [2, 6]:
            download_url = data.get("url")
            if not download_url:
                return jsonify({"error": 1})

            # 1. Get storage path
            res = supabase.table('course_learning_plans').select('filename').eq('id', plan_id).single().execute()
            if not res.data:
                return jsonify({"error": 1, "message": "Plan not found"})
            
            storage_path = res.data['filename']

            # 2. Download new file
            resp = requests.get(download_url, timeout=30)
            resp.raise_for_status()
            new_file_data = resp.content

            # 3. Overwrite in Supabase
            supabase.storage.from_(STORAGE_BUCKET_NAME).update(
                path=storage_path,
                file=new_file_data,
                file_options={"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "upsert": "true"}
            )
            
            current_app.logger.info(f"Dean updated CLP {plan_id}")
            return jsonify({"error": 0})

        return jsonify({"error": 0})

    except Exception as e:
        current_app.logger.error(f"Dean Callback Error: {e}")
        return jsonify({"error": 1})