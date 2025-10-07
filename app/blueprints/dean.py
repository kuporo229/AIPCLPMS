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