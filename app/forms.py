# app/forms.py

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import (StringField, PasswordField, SubmitField, SelectField,
                     TextAreaField, HiddenField, EmailField)
from wtforms.validators import DataRequired, Length, EqualTo, Regexp, Email, Optional

# app/forms.py

# ... existing imports ...
from wtforms import (StringField, PasswordField, SubmitField, SelectField,
                     TextAreaField, HiddenField, EmailField)
from wtforms.validators import DataRequired, Length, EqualTo, Regexp, Email, Optional
# ... existing forms ...

# --- NEW FORM FOR ADMIN TEMPLATE EDITING ---
class TemplateEditForm(FlaskForm):
    # This field will hold the extracted text content of the DOCX template
    content = TextAreaField('Template Content', validators=[DataRequired()], 
                            render_kw={"rows": 25, "placeholder": "Paste the complete Course Learning Plan template text here..."})
    submit = SubmitField('Update Template')

# --- WTFORMS (Adjusted for Supabase Auth) ---
class LoginForm(FlaskForm):
    email = EmailField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Sign In')

class SignupForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired(), Length(max=80)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(max=80)])
    username = StringField('Username', validators=[DataRequired(), Length(min=4, max=80), Regexp('^[A-Za-z][A-Za-z0-9_.]*$', 0, 'Usernames must have only letters, numbers, dots or underscores')])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=120)])
    title = StringField('Title (e.g., RMT, LPT, DIT, Ph.D.)', validators=[Optional(), Length(max=50)])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=8, message="Password must be at least 8 characters long.")])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password', message='Passwords must match.')])
    department = SelectField('Department (for Teachers)', choices=[('Department of Information Technology', 'Department of Information Technology'), ('Department of Engineering', 'Department of Engineering'), ('Department of Business', 'Department of Business'), ('Department of Arts and Sciences', 'Department of Arts and Sciences')], validators=[Optional()])
    submit = SubmitField('Register Account')

class ChangePasswordForm(FlaskForm):
    # NOTE: Supabase Auth doesn't require the current password to set a new one when the user is already logged in.
    new_password = PasswordField('New Password', validators=[DataRequired(), Length(min=8, message="New password must be at least 8 characters long.")])
    confirm_new_password = PasswordField('Confirm New Password', validators=[DataRequired(), EqualTo('new_password', message='New passwords must match.')])
    submit = SubmitField('Change Password')

class CLPUploadForm(FlaskForm):
    department = SelectField('Department', choices=[('Department of Information Technology', 'Department of Information Technology'), ('Department of Engineering', 'Department of Engineering'), ('Department of Business', 'Department of Business'), ('Department of Arts and Sciences', 'Department of Arts and Sciences')], validators=[DataRequired()])
    subject = StringField('Subject Name', validators=[DataRequired(), Length(min=3, max=100)])
    content = TextAreaField('Course Learning Plan Content (Optional)')
    file = FileField('Upload CLP Document (Optional)', validators=[FileAllowed(['docx', 'pdf'], 'Only .docx and .pdf files are allowed!')])
    submit = SubmitField('Upload Plan')

class CLPUpdateForm(CLPUploadForm):
    submit = SubmitField('Update Plan')

class CLPGenerateForm(FlaskForm):
    subject_name = StringField('Subject Name', validators=[DataRequired(), Length(min=5, max=100)], render_kw={"placeholder": "e.g., Introduction to HCI"})
    department = SelectField('Department', choices=[('Department of Information Technology', 'Department of Information Technology'), ('Department of Engineering', 'Department of Engineering'), ('Department of Business', 'Department of Business'), ('Department of Arts and Sciences', 'Department of Arts and Sciences')], validators=[DataRequired()])
    submit = SubmitField('Generate with AI')

class DeanReviewForm(FlaskForm):
    comments = TextAreaField('Comments (Optional)', render_kw={"placeholder": "Provide feedback for revision..."})
    submit_approve = SubmitField('Approve Plan')
    submit_return = SubmitField('Return for Revision')

class ApproveUserForm(FlaskForm):
    user_id = HiddenField(validators=[DataRequired()])
    role = SelectField('Assign Role', choices=[('teacher', 'Teacher'), ('dean', 'Dean')], validators=[DataRequired()])
    assigned_department = SelectField('Assign Department (for Teachers)', choices=[
        ('', 'No Department (e.g., Dean)'),
        ('Department of Information Technology', 'Department of Information Technology'),
        ('Department of Engineering', 'Department of Engineering'),
        ('Department of Business', 'Department of Business'),
        ('Department of Arts and Sciences', 'Department of Arts and Sciences')
    ], validators=[Optional()])
    submit = SubmitField('Approve & Assign Role')