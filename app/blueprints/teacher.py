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
teacher_bp = Blueprint('teacher', __name__)


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