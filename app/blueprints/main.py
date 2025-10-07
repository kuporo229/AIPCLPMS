# app/blueprints/main.py

from flask import Blueprint, render_template, redirect, url_for, session, jsonify, request, flash
from supabase import PostgrestAPIError
from app import supabase
from app.decorators import login_required
from app.utils import parse_supabase_timestamp

# Create a Blueprint instance
main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('main.dashboard'))
    return redirect(url_for('auth.login'))


@main_bp.route('/dashboard')
@login_required
def dashboard():
    role = session.get('role')
    user_id = session.get('user_id')
    unread_notifications_count = 0
    if user_id:
        try:
            count_res = supabase.table('notifications').select('id', count='exact').eq('user_id', user_id).eq('is_read', False).execute()
            unread_notifications_count = count_res.count
        except PostgrestAPIError:
            pass  # Fail silently if notifications table is inaccessible
    
    if role == 'admin':
        return redirect(url_for('admin.admin_dashboard'))
    elif role == 'teacher':
        return render_template('teacher_dashboard.html', unread_notifications=unread_notifications_count)
    elif role == 'dean':
        return render_template('dean_dashboard.html', unread_notifications=unread_notifications_count)
    else:
        # A new user who is not yet approved might not have a role.
        flash("Your role is not defined. Please contact an administrator.", "warning")
        return redirect(url_for('auth.login'))
        
# --- NOTIFICATION ROUTES ---

@main_bp.route('/notifications')
@login_required
def list_notifications():
    notif_res = supabase.table('notifications').select('*').eq('user_id', session['user_id']).order('timestamp', desc=True).execute()

    # --- FIX: Convert timestamp strings to datetime objects for the template ---
    notifications_with_dates = parse_supabase_timestamp(notif_res.data, 'timestamp')

    return render_template('notifications.html', notifications=notifications_with_dates)
@main_bp.route('/check_notifications')
@login_required
def check_notifications():
    count_res = supabase.table('notifications').select('id', count='exact').eq('user_id', session['user_id']).eq('is_read', False).execute()
    return jsonify({'unread_count': count_res.count})

@main_bp.route('/notifications/mark_read/<int:notification_id>', methods=['POST'])
@login_required
def mark_notification_read(notification_id):
    supabase.table('notifications').update({'is_read': True}).eq('id', notification_id).eq('user_id', session['user_id']).execute()
    flash('Notification marked as read.', 'info')
    return redirect(url_for('main.list_notifications'))

@main_bp.route('/notifications/mark_all_read', methods=['POST'])
@login_required
def mark_all_read():
    try:
        supabase.table('notifications').update({'is_read': True}).eq('user_id', session['user_id']).execute()
        flash('All notifications have been marked as read.', 'info')
    except Exception as e:
        flash(f'Error marking all notifications as read: {e}', 'danger')
    return redirect(url_for('main.list_notifications'))

@main_bp.route('/notifications/delete_read', methods=['POST'])
@login_required
def delete_read_notifications():
    try:
        # Delete only notifications that are read
        supabase.table('notifications').delete().eq('user_id', session['user_id']).eq('is_read', True).execute()
        flash('All read notifications have been deleted.', 'success')
    except Exception as e:
        flash(f'Error deleting read notifications: {e}', 'danger')
    return redirect(url_for('main.list_notifications'))
