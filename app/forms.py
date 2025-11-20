# app/forms.py

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import (StringField, PasswordField, SubmitField, SelectField,
                     TextAreaField, HiddenField, EmailField)
from wtforms.validators import DataRequired, Length, EqualTo, Regexp, Email, Optional
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, SelectField, FileField, TextAreaField, IntegerField
from wtforms.validators import DataRequired, Email, Length, Optional, NumberRange

# app/forms.py

# ... existing imports ...
from wtforms import (StringField, PasswordField, SubmitField, SelectField,
                     TextAreaField, HiddenField, EmailField)
from wtforms.validators import DataRequired, Length, EqualTo, Regexp, Email, Optional
from app import supabase
DEPARTMENT_CHOICES = [
    ('Department of Information Technology', 'Department of Information Technology'),
    ('Department of Engineering', 'Department of Engineering'),
    ('Department of Business', 'Department of Business'),
    ('Department of Arts and Sciences', 'Department of Arts and Sciences')
]
# ... existing forms ...
class GenerateAIForm(FlaskForm):
    department = SelectField('Department', choices=[], validators=[DataRequired()])
    course_code = StringField('Course Code', validators=[DataRequired(), Length(max=20)])
    course_title = StringField('Course Title', validators=[DataRequired(), Length(max=255)])
    course_description = TextAreaField('Course Description', validators=[DataRequired()])
    type_of_course = StringField('Type of Course (e.g., Lecture, Laboratory)', validators=[DataRequired(), Length(max=50)])
    unit = IntegerField('Unit', validators=[DataRequired(), NumberRange(min=1, max=10)])
    pre_requisite = StringField('Pre-Requisite', validators=[Optional(), Length(max=100)])
    co_requisite = StringField('Co-Requisite', validators=[Optional(), Length(max=100)])
    credit = IntegerField('Credit', validators=[DataRequired(), NumberRange(min=0, max=10)]) # Could be float too
    contact_hours_per_week = StringField('Contact Hours Per Week (e.g., 3 Lecture, 2 Lab)', validators=[DataRequired(), Length(max=50)])
    class_schedule = StringField('Class Schedule (e.g., MWF 8:00 AM - 9:00 AM)', validators=[DataRequired(), Length(max=100)])
    room_assignment = StringField('Room Assignment', validators=[Optional(), Length(max=50)])
    submit = SubmitField('CREATE')
    def __init__(self, *args, **kwargs):
        super(GenerateAIForm, self).__init__(*args, **kwargs)
        try:
            res = supabase.table('departments').select('name').order('name').execute()
            self.department.choices = [(d['name'], d['name']) for d in res.data]
        except:
            self.department.choices = []

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
    department = SelectField('Department (for Teachers)', choices=[], validators=[Optional()])
    submit = SubmitField('Register Account')

    def __init__(self, *args, **kwargs):
        super(SignupForm, self).__init__(*args, **kwargs)
        try:
            res = supabase.table('departments').select('name').order('name').execute()
            self.department.choices = [(d['name'], d['name']) for d in res.data]
        except:
            self.department.choices = []

class ChangePasswordForm(FlaskForm):
    # NOTE: Supabase Auth doesn't require the current password to set a new one when the user is already logged in.
    new_password = PasswordField('New Password', validators=[DataRequired(), Length(min=8, message="New password must be at least 8 characters long.")])
    confirm_new_password = PasswordField('Confirm New Password', validators=[DataRequired(), EqualTo('new_password', message='New passwords must match.')])
    submit = SubmitField('Change Password')

class CLPUploadForm(FlaskForm):
    department = SelectField('Department', choices=[], validators=[DataRequired()])
    subject = StringField('Subject Name', validators=[DataRequired(), Length(min=3, max=100)])

    content = TextAreaField('Course Learning Plan Content (Optional)')
    file = FileField('Upload CLP Document (Optional)', validators=[FileAllowed(['docx', 'pdf'], 'Only .docx and .pdf files are allowed!')])
    submit = SubmitField('Upload Plan')
    def __init__(self, *args, **kwargs):
        super(CLPUploadForm, self).__init__(*args, **kwargs)
        try:
            res = supabase.table('departments').select('name').order('name').execute()
            self.department.choices = [(d['name'], d['name']) for d in res.data]
        except:
            self.department.choices = []

class CLPUpdateForm(CLPUploadForm):
    submit = SubmitField('Update Plan')

class CLPGenerateForm(FlaskForm):
    subject_name = StringField('Subject Name', validators=[DataRequired(), Length(min=5, max=100)], render_kw={"placeholder": "e.g., Introduction to HCI"})
    department = SelectField('Department', choices=DEPARTMENT_CHOICES, validators=[DataRequired()])
    
    submit = SubmitField('Generate with AI')

class DeanReviewForm(FlaskForm):
    comments = TextAreaField('Comments (Optional)', render_kw={"placeholder": "Provide feedback for revision..."})
    submit_approve = SubmitField('Approve Plan')
    submit_return = SubmitField('Return for Revision')

class ApproveUserForm(FlaskForm):
    user_id = HiddenField(validators=[DataRequired()])
    role = SelectField('Assign Role', choices=[('teacher', 'Teacher'), ('dean', 'Dean')], validators=[DataRequired()])
    assigned_department = SelectField('Assign Department', choices=[], validators=[Optional()])
    submit = SubmitField('Approve & Assign Role')
    def __init__(self, *args, **kwargs):
        super(ApproveUserForm, self).__init__(*args, **kwargs)
        try:
            res = supabase.table('departments').select('name').order('name').execute()
            choices = [(d['name'], d['name']) for d in res.data]
            # Add the "No Department" option at the start
            self.assigned_department.choices = [('', 'No Department (e.g., Dean)')] + choices
        except:
            self.assigned_department.choices = [('', 'Error Loading Departments')]

# ... existing code ...

# Open app/forms.py and add this class at the bottom:

# ... existing code ...

class EditUserForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired(), Length(max=80)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(max=80)])
    title = StringField('Title', validators=[Optional(), Length(max=50)])
    role = SelectField('Role', choices=[('teacher', 'Teacher'), ('dean', 'Dean')], validators=[DataRequired()])
    department = SelectField('Department', choices=[], validators=[Optional()])
    submit = SubmitField('Save Changes')
    def __init__(self, *args, **kwargs):
        super(EditUserForm, self).__init__(*args, **kwargs)
        try:
            res = supabase.table('departments').select('name').order('name').execute()
            choices = [(d['name'], d['name']) for d in res.data]
            self.department.choices = choices + [('', 'None')]
        except:
            self.department.choices = [('', 'None')]

class DepartmentForm(FlaskForm):
    name = StringField('Department Name', validators=[DataRequired(), Length(max=100)])
    submit = SubmitField('Add Department')

class TemplateUploadForm(FlaskForm):
    name = StringField('Template Name', validators=[DataRequired(), Length(max=100)])
    file = FileField('Template File (.docx)', validators=[DataRequired(), FileAllowed(['docx'], 'Only .docx files allowed!')])
    department = SelectField('Assign to Department', choices=[], validators=[Optional()])
    is_default = SelectField('Set as Global Default?', choices=[('no', 'No'), ('yes', 'Yes')], validators=[Optional()])
    submit = SubmitField('Upload Template')

    def __init__(self, *args, **kwargs):
        super(TemplateUploadForm, self).__init__(*args, **kwargs)
        try:
            # Load departments for assignment
            res = supabase.table('departments').select('id, name').order('name').execute()
            # Choices format: (id, name)
            self.department.choices = [('', 'None (General Use)')] + [(str(d['id']), d['name']) for d in res.data]
        except:
            self.department.choices = [('', 'None')]
class SystemSettingsForm(FlaskForm):
    # We use TextAreaField for large blocks of text
    prompt_po_io = TextAreaField('PO-IO Mapping Prompt', validators=[DataRequired()], render_kw={"rows": 10})
    prompt_co_po = TextAreaField('CO-PO Mapping Prompt', validators=[DataRequired()], render_kw={"rows": 10})
    prompt_weekly = TextAreaField('Weekly Breakdown Prompt', validators=[DataRequired()], render_kw={"rows": 15})
    
    submit = SubmitField('Update Settings')