# app/blueprints/auth.py

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, app
from app import supabase
from app.forms import LoginForm, SignupForm
from app.decorators import login_required

# Create a Blueprint instance for authentication
auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('main.dashboard'))
    form = LoginForm()
    if form.validate_on_submit():
        try:
            # Step 1: Authenticate with Supabase Auth
            auth_response = supabase.auth.sign_in_with_password({
                "email": form.email.data,
                "password": form.password.data
            })
            user_id = auth_response.user.id
            
            # Step 2: Fetch the user's profile from our public 'users' table
            profile_res = supabase.table('users').select('*').eq('id', user_id).single().execute()
            profile = profile_res.data
            
            if profile and profile.get('approved'):
                session['user_id'] = profile['id']
                session['role'] = profile['role']
                session['username'] = profile['username']
                flash('Login successful!', 'success')
                return redirect(url_for('main.dashboard'))
            elif profile and not profile.get('approved'):
                flash('Your account is pending approval.', 'warning')
            else:
                flash('User profile not found or not approved.', 'danger')
        except Exception:
            flash('Invalid email or password.', 'danger')
    return render_template('login.html', form=form)

@auth_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    form = SignupForm()
    if form.validate_on_submit():
        # Check if username or email already exists in our public users table
        existing_user_res = supabase.table('users').select('id').or_(f"username.eq.{form.username.data},email.eq.{form.email.data}").execute()
        if existing_user_res.data:
            flash('Username or email already exists. Please choose a different one or login.', 'danger')
            return redirect(url_for('auth.signup'))
            
        try:
            # Step 1: Create the user in Supabase Auth
            auth_user = supabase.auth.sign_up({
                'email': form.email.data,
                'password': form.password.data
            })
            
            if auth_user.user:
                # Step 2: Create the user profile in the public 'users' table
                profile_data = {
                    'id': auth_user.user.id, # Link to the auth user
                    'username': form.username.data,
                    'first_name': form.first_name.data,
                    'last_name': form.last_name.data,
                    'email': form.email.data,
                    'title': form.title.data,
                    'role': 'teacher', # Default role
                    'approved': False, # Default not approved
                    'assigned_department': form.department.data if form.department.data else None
                }
                supabase.table('users').insert(profile_data).execute()
                flash('Registration successful! Your account is pending administrator approval.', 'info')
                return redirect(url_for('auth.login'))
            else:
                 flash('Could not create authentication user. Please try again.', 'danger')
        except Exception as e:
            app.logger.error(f"Signup failed: {e}")
            flash('An error occurred during registration. Please check the server logs.', 'danger')
    return render_template('signup.html', form=form)

@auth_bp.route('/logout')
@login_required
def logout():
    try:
        supabase.auth.sign_out()
    except Exception as e:
        # app.logger is not available directly, use current_app
        pass # Log in the main app
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))