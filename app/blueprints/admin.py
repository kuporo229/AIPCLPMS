# app/blueprints/admin.py

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app, jsonify
from supabase import PostgrestAPIError
from app import supabase
from app.decorators import login_required, roles_required, admin_required
from app.forms import ApproveUserForm, TemplateEditForm
from werkzeug.utils import secure_filename
import re
from docx import Document
import io
from io import BytesIO
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
import html

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# Configuration
STORAGE_BUCKET_NAME = 'clp_files'
TEMPLATE_KEY = "PBSIT/PBSIT-001-LP-20242.docx"

def extract_placeholders(doc: Document) -> list:
    """Extract all {{placeholder}} tags from a document"""
    tags = set()
    for para in doc.paragraphs:
        matches = re.findall(r"\{\{(.*?)\}\}", para.text)
        tags.update(matches)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    matches = re.findall(r"\{\{(.*?)\}\}", para.text)
                    tags.update(matches)
    return sorted(tags)

def docx_to_html(doc: Document) -> str:
    """Convert DOCX document to HTML for editing"""
    html_parts = []
    
    for para in doc.paragraphs:
        if not para.text.strip():
            html_parts.append('<p><br></p>')
            continue
            
        # Check alignment
        align = ''
        if para.alignment == WD_ALIGN_PARAGRAPH.CENTER:
            align = ' style="text-align: center;"'
        elif para.alignment == WD_ALIGN_PARAGRAPH.RIGHT:
            align = ' style="text-align: right;"'
        elif para.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY:
            align = ' style="text-align: justify;"'
        
        # Process runs (formatted text segments)
        para_html = []
        for run in para.runs:
            text = html.escape(run.text)
            
            if run.bold:
                text = f'<strong>{text}</strong>'
            if run.italic:
                text = f'<em>{text}</em>'
            if run.underline:
                text = f'<u>{text}</u>'
                
            para_html.append(text)
        
        html_parts.append(f'<p{align}>{"".join(para_html)}</p>')
    
    # Handle tables
    for table in doc.tables:
        table_html = '<table style="border-collapse: collapse; width: 100%; border: 1px solid #ddd;">'
        for row in table.rows:
            table_html += '<tr>'
            for cell in row.cells:
                cell_content = []
                for para in cell.paragraphs:
                    cell_content.append(para.text)
                cell_text = '<br>'.join(cell_content)
                table_html += f'<td style="border: 1px solid #ddd; padding: 8px;">{html.escape(cell_text)}</td>'
            table_html += '</tr>'
        table_html += '</table>'
        html_parts.append(table_html)
    
    return '\n'.join(html_parts)

def html_to_docx(html_content: str) -> Document:
    """Convert HTML back to DOCX document"""
    from html.parser import HTMLParser
    
    doc = Document()
    
    class DocxHTMLParser(HTMLParser):
        def __init__(self, document):
            super().__init__()
            self.doc = document
            self.current_para = None
            self.current_run = None
            self.bold_stack = []
            self.italic_stack = []
            self.underline_stack = []
            self.in_table = False
            self.current_table = None
            self.current_row = None
            self.current_cell = None
            
        def handle_starttag(self, tag, attrs):
            if tag == 'p':
                self.current_para = self.doc.add_paragraph()
                # Check for alignment
                for attr, value in attrs:
                    if attr == 'style' and 'text-align' in value:
                        if 'center' in value:
                            self.current_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        elif 'right' in value:
                            self.current_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                        elif 'justify' in value:
                            self.current_para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            elif tag in ['strong', 'b']:
                self.bold_stack.append(True)
            elif tag in ['em', 'i']:
                self.italic_stack.append(True)
            elif tag == 'u':
                self.underline_stack.append(True)
            elif tag == 'br':
                if self.current_para:
                    self.current_para.add_run('\n')
            elif tag == 'table':
                self.in_table = True
                # Count rows and columns from HTML
                self.current_table = self.doc.add_table(rows=1, cols=1)
                self.current_table.style = 'Table Grid'
            elif tag == 'tr' and self.in_table:
                if self.current_table and len(self.current_table.rows) == 0:
                    self.current_row = self.current_table.rows[0]
                else:
                    self.current_row = self.current_table.add_row()
            elif tag == 'td' and self.in_table and self.current_row:
                if len(self.current_row.cells) == 0:
                    self.current_cell = self.current_row.cells[0]
                else:
                    # Add column if needed
                    try:
                        cell_idx = len([c for c in self.current_row.cells if c.text])
                        self.current_cell = self.current_row.cells[cell_idx]
                    except:
                        self.current_cell = None
                        
        def handle_endtag(self, tag):
            if tag == 'p':
                self.current_para = None
            elif tag in ['strong', 'b']:
                if self.bold_stack:
                    self.bold_stack.pop()
            elif tag in ['em', 'i']:
                if self.italic_stack:
                    self.italic_stack.pop()
            elif tag == 'u':
                if self.underline_stack:
                    self.underline_stack.pop()
            elif tag == 'table':
                self.in_table = False
                self.current_table = None
            elif tag == 'tr':
                self.current_row = None
            elif tag == 'td':
                self.current_cell = None
                
        def handle_data(self, data):
            if not data.strip():
                return
                
            if self.current_cell and self.in_table:
                para = self.current_cell.paragraphs[0] if self.current_cell.paragraphs else self.current_cell.add_paragraph()
                run = para.add_run(data)
            elif self.current_para:
                run = self.current_para.add_run(data)
                if self.bold_stack:
                    run.bold = True
                if self.italic_stack:
                    run.italic = True
                if self.underline_stack:
                    run.underline = True
    
    parser = DocxHTMLParser(doc)
    parser.feed(html_content)
    
    return doc

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

@admin_bp.route('/manage_template', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_template():
    if request.method == 'POST':
        try:
            # Get HTML content from TinyMCE
            html_content = request.form.get('content', '').strip()
            
            if not html_content:
                flash("No content received from editor.", "danger")
                return redirect(url_for('admin.manage_template'))
            
            # Convert HTML back to DOCX
            new_doc = html_to_docx(html_content)
            
            # Save to BytesIO
            docx_stream = BytesIO()
            new_doc.save(docx_stream)
            docx_stream.seek(0)
            
            # Upload to Supabase (overwrite existing)
            supabase.storage.from_(STORAGE_BUCKET_NAME).update(
                path=TEMPLATE_KEY,
                file=docx_stream.read(),
                file_options={"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
            )
            
            flash("Template updated successfully! Changes will be used for future AI generations.", "success")
            return redirect(url_for('admin.manage_template'))
            
        except Exception as e:
            current_app.logger.error(f"Error updating template: {e}")
            flash(f"Error updating template: {str(e)}", "danger")
            return redirect(url_for('admin.manage_template'))
    
    # GET request - load and display template
    try:
        # Fetch template from Supabase
        template_bytes = supabase.storage.from_(STORAGE_BUCKET_NAME).download(TEMPLATE_KEY)
        
        if not template_bytes:
            flash("Template file not found in storage.", "danger")
            return render_template('admin_edit_template.html', 
                                 docx_html="<p>Template not found. Please upload a template first.</p>",
                                 placeholders=[],
                                 current_template_key=TEMPLATE_KEY)
        
        # Load DOCX
        doc = Document(BytesIO(template_bytes))
        
        # Convert to HTML for editing
        docx_html = docx_to_html(doc)
        
        # Extract placeholders for reference
        placeholders = extract_placeholders(doc)
        
        return render_template('admin_edit_template.html',
                             docx_html=docx_html,
                             placeholders=placeholders,
                             current_template_key=TEMPLATE_KEY)
        
    except Exception as e:
        current_app.logger.error(f"Error loading template: {e}")
        flash(f"Error loading template: {str(e)}", "danger")
        return render_template('admin_edit_template.html',
                             docx_html="<p>Error loading template.</p>",
                             placeholders=[],
                             current_template_key=TEMPLATE_KEY)

@admin_bp.route('/download_template')
@login_required
@admin_required
def download_template():
    """Download the current template file"""
    try:
        from flask import Response
        
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
        # Read file content
        file_content = file.read()
        
        # Upload to Supabase (overwrite)
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