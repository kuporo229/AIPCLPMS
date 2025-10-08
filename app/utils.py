# app/utils.py

import os
import io
import json
from datetime import datetime
from threading import Thread
import google.generativeai as genai
from google.generativeai import GenerativeModel
from docx import Document
from flask import current_app, session, flash, url_for, redirect, jsonify, Response
import logging

from werkzeug.utils import secure_filename
# Import the exception class from the library
from supabase import PostgrestAPIError
# Import the supabase client and other global variables from your app's main factory file
from app import supabase, STORAGE_BUCKET_NAME, ALLOWED_EXTENSIONS
import sys
from postgrest.exceptions import APIError as PostgrestAPIError

# Re-import static data from the app factory
from app import PROGRAM_OUTCOMES, COURSE_OUTCOMES, INSTITUTIONAL_OUTCOMES_HEADERS, PROGRAM_OUTCOMES_HEADERS

from supabase import create_client

# Normal client (anon/public key) - for user-facing queries
from app import supabase  
# Ensure logging is configured properly
if current_app:
    current_app.logger.setLevel(logging.INFO)
    if not current_app.logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        current_app.logger.addHandler(handler)

# Create a separate service client (bypasses RLS)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")  # store this in .env, NEVER frontend

supabase_service = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# app/utils.py

# ... existing imports (make sure you have `docx` imported)



# ... existing utility functions ...

# --- NEW DOCX UTILITY FUNCTIONS ---

def get_template_filepath():
    """Returns the absolute path to the official CLP template file."""
    # NOTE: You must ensure this file path is correct relative to where your Flask app runs
    # Assuming the template file is in the root directory for now
    template_filename = 'PBSIT-001-LP-20242 copy.docx'
    # Use current_app.root_path to find the file
    return os.path.join(os.path.dirname(__file__), 'clp_templates', 'PBSIT-001-LP-20242 copy.docx')



import base64

def load_template_content(filename):
    """
    Load the content of a .docx template as HTML/text for inline editing.
    Right now this just returns plain text, but you can later improve it
    with a docx → HTML converter (like python-docx, pydocx, or Mammoth).
    """
    template_path = os.path.join(os.path.dirname(__file__), 'clp_templates', filename)

    if not os.path.exists(template_path):
        return ""

    try:
        from docx import Document
        doc = Document(template_path)
        # Join paragraphs with line breaks
        return "\n".join([p.text for p in doc.paragraphs])
    except Exception as e:
        # Fallback: just return empty if parsing fails
        return ""
    
def get_template_content():
    """
    Reads DOCX and returns full HTML with headers, footers, text formatting, tables, and images.
    Works on all python-docx versions (no xpath).
    """
    try:
        doc_path = get_template_filepath()
        document = Document(doc_path)
        content = []

        # --- Headers ---
        for section in document.sections:
            header = section.header
            if header and header.paragraphs:
                content.append('<div style="border-bottom:1px solid #000; margin-bottom:10px;">')
                for para in header.paragraphs:
                    content.append(_format_paragraph_with_images(para))
                content.append('</div>')

        # --- Body paragraphs ---
        for para in document.paragraphs:
            content.append(_format_paragraph_with_images(para))

        # --- Tables ---
        for table in document.tables:
            table_html = '<table style="border-collapse:collapse; width:100%; margin:10px 0;">'
            for row in table.rows:
                table_html += "<tr>"
                for cell in row.cells:
                    cell_text = ''.join(_format_paragraph_with_images(p) for p in cell.paragraphs)
                    table_html += f'<td style="border:1px solid #444; padding:5px; vertical-align:top;">{cell_text}</td>'
                table_html += "</tr>"
            table_html += "</table>"
            content.append(table_html)

        # --- Footers ---
        for section in document.sections:
            footer = section.footer
            if footer and footer.paragraphs:
                content.append('<div style="border-top:1px solid #000; margin-top:10px;">')
                for para in footer.paragraphs:
                    content.append(_format_paragraph_with_images(para))
                content.append('</div>')

        return "\n".join(content)

    except FileNotFoundError:
        current_app.logger.error(f"Template file not found at: {doc_path}")
        return "ERROR: Template file not found."
    except Exception as e:
        current_app.logger.error(f"Error reading DOCX: {e}")
        return f"ERROR: Could not read template content. Details: {e}"


def _format_paragraph_with_images(para):
    """
    Converts a DOCX paragraph into HTML, scanning for images by walking the XML tree
    (avoids xpath completely).
    """
    formatted_text = ""

    for run in para.runs:
        # --- Find embedded images by iterating XML elements ---
        for child in run._element.iter():
            if child.tag.endswith("blip"):  # <a:blip> element contains the image relationship ID
                rId = child.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
                if rId:
                    image_part = run.part.related_parts.get(rId)
                    if image_part:
                        image_data = image_part.blob
                        encoded_image = base64.b64encode(image_data).decode("utf-8")
                        formatted_text += f'<img src="data:image/png;base64,{encoded_image}" style="max-height:100px; margin:5px;">'

        # --- Handle text formatting ---
        if not run.text.strip():
            continue
        run_text = run.text
        if run.bold:
            run_text = f"<b>{run_text}</b>"
        if run.italic:
            run_text = f"<i>{run_text}</i>"
        if run.underline:
            run_text = f"<u>{run_text}</u>"
        formatted_text += run_text

    # --- Paragraph alignment ---
    align_map = {0: "left", 1: "center", 2: "right", 3: "justify"}
    align = para.alignment
    align_style = ""
    if align is not None and align in align_map:
        align_style = f"text-align:{align_map[align]};"

    return f'<p style="{align_style} margin:5px 0;">{formatted_text}</p>'

def overwrite_template_content(new_content):
    """Overwrites the content of the DOCX template with new text."""
    try:
        if not new_content or len(new_content.strip()) < 10:
            current_app.logger.warning("Refused to overwrite template: empty content received.")
            return False
        
        doc_path = get_template_filepath()
        
        # 1. Create a brand new, empty document
        new_document = Document()
        
        # 2. Split the content by double-newline to represent new paragraphs
        paragraphs = new_content.split('\n\n')
        
        # 3. Add each part as a new paragraph to the new document
        for text in paragraphs:
            # Replace single newlines within a paragraph with a line break (for soft wrapping)
            text_with_breaks = text.replace('\n', '\n') 
            new_document.add_paragraph(text_with_breaks)
            
        # 4. Save the new document, overwriting the old template
        new_document.save(doc_path)
        return True
    except Exception as e:
        current_app.logger.error(f"Error writing to template DOCX: {e}")
        return False

# --- DOCX GENERATOR & HELPER FUNCTIONS ---
def parse_supabase_timestamp(data_list, field_name='timestamp'):
    """Converts ISO 8601 timestamp strings from Supabase into Python datetime objects."""
    for item in data_list:
        if item.get(field_name):
            # Handle potential timezone info and fractional seconds
            iso_string = item[field_name].split('+')[0].split('.')[0]
            try:
                item[field_name] = datetime.fromisoformat(iso_string)
            except ValueError:
                # Fallback for unexpected formats, though less likely
                pass
    return data_list

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def replace_text_in_paragraph(paragraph, replacements):
    for key, val in replacements.items():
        if key in paragraph.text:
            inline = paragraph.runs
            full_text = "".join(run.text for run in inline)
            if key in full_text:
                for run in inline:
                    if key in run.text:
                        run.text = run.text.replace(key, str(val))

def replace_text_in_cell(cell, replacements):
    for paragraph in cell.paragraphs:
        replace_text_in_paragraph(paragraph, replacements)

def replace_placeholders(doc, replacements):
    for para in doc.paragraphs:
        replace_text_in_paragraph(para, replacements)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                replace_text_in_cell(cell, replacements)
    return doc

def flatten_json(data):
    out = {}
    def flatten(x, name=''):
        if isinstance(x, dict):
            for a in x:
                flatten(x[a], name + a)
        elif isinstance(x, list):
            out[name] = "\n".join(map(str, x))
        else:
            out[name] = x
    flatten(data)
    return out

def create_notification(user_id, message):
    try:
        supabase.table('notifications').insert({
            'user_id': user_id,
            'message': message
        }).execute()
    except PostgrestAPIError as e:
        current_app.logger.error(f"Failed to create notification for user {user_id}: {e.message}")

def get_current_user_profile():
    user_id = session.get('user_id')
    if not user_id: return None
    try:
        res = supabase.table('users').select('*').eq('id', user_id).single().execute()
        return res.data
    except PostgrestAPIError as e:
        current_app.logger.error(f"Could not fetch profile for user {user_id}: {e.message}")
        return None

# --- AI GENERATION BACKGROUND TASK (REFACTORED AND IMPROVED) ---
def start_clp_generation(user_id, course_data):
    """A public function to start the background thread."""
    thread = Thread(target=generate_clp_background_task, args=(current_app.app_context(), user_id, course_data))
    thread.daemon = True
    thread.start()

def generate_clp_background_task(app_context, user_id, course_data):
    subject_name = course_data['subject']
    department = course_data['department']
  
    with app_context:
        try:
            current_app.logger.info(f"--- [AI DEBUG] BG Task started for user {user_id}, subject '{subject_name}'. ---")
            
            model_instance = GenerativeModel('gemini-2.5-flash')
            clp_data = {}
            
            # --- Step 1: Generate Basic Info and References (Text Generation) ---

            # --- Step 2: Generate Program to Institutional Outcomes Mapping ---
            current_app.logger.info("--- [AI DEBUG] Step 2: Generating Program to Institutional Outcomes Mapping... ---")
            po_io_prompt = f"""
You are an expert academic planner. Your task is to map the alignment between BSIT Program Outcomes (PO) and Institutional Outcomes (IO) for an IT curriculum.

IMPORTANT INSTRUCTIONS:
1. For each Program Outcome (IT01–IT13) and Institutional Outcome (T, R1, I1, R2, I2, C, H), assign exactly one of these: "✔" or " ".
2. The matrix MUST NOT be uniform (e.g., all "✔" or all " ").
3. Your output MUST be a valid JSON object in the following format:
{{
    "IT01_T": "✔" or " ",
    "IT01_R1": "✔" or " ",
    ...
    "IT13_H": "✔" or " "
}}
Output ONLY a JSON object with NO other text or markdown.
"""
            po_io_schema_properties = {f"{po_code}_{io_header}": {"type": "STRING", "enum": ["✔", " "]} for po_code in PROGRAM_OUTCOMES_HEADERS for io_header in INSTITUTIONAL_OUTCOMES_HEADERS}
            step2_generation_config = {"response_mime_type": "application/json", "response_schema": {"type": "OBJECT", "properties": po_io_schema_properties, "required": list(po_io_schema_properties.keys())}}
            try:
                response_po_io = model_instance.generate_content(contents=[po_io_prompt], generation_config=step2_generation_config)
                clp_data.update(json.loads(response_po_io.text))
                current_app.logger.info("--- [AI DEBUG] Step 2: PO to IO mapping generated successfully. ---")
            except genai.types.generation_types.BlockedPromptException as e:
                raise ValueError(f"AI generation failed (Blocked): {e.message}")
            except Exception as e:
                raise RuntimeError(f"AI generation failed (Step 2): {e}")

            # --- Step 3: Generate Course to Program Outcomes Mapping ---
            current_app.logger.info("--- [AI DEBUG] Step 3: Generating Course to Program Outcomes Mapping... ---")
            course_outcomes_string = ", ".join([f"{co['code']}: {co['description']}" for co in COURSE_OUTCOMES])
            program_outcomes_string = ", ".join([f"{po['code']}: {po['description']}" for po in PROGRAM_OUTCOMES])
            co_po_prompt = f"""
Given the Course Outcomes (CO) and BSIT Program Outcomes (PO), determine the relationship for each pair.
Course Outcomes: {course_outcomes_string}
Program Outcomes: {program_outcomes_string}

For each combination of CO and PO, assign "E" if it *Enables* the PO, "I" if it *Introduces* it, or " " for no alignment.
Your output MUST be a JSON object with keys like 'L012_IT01' and values of "E", "I", or " ".

Output ONLY a JSON object with NO other text or markdown.
"""
            co_po_schema_properties = {f"{co_code_obj['code']}_{po_code}": {"type": "STRING", "enum": ["E", "I", " "]} for co_code_obj in COURSE_OUTCOMES for po_code in PROGRAM_OUTCOMES_HEADERS}
            step3_generation_config = {"response_mime_type": "application/json", "response_schema": {"type": "OBJECT", "properties": co_po_schema_properties, "required": list(co_po_schema_properties.keys())}}
            try:
                response_co_po = model_instance.generate_content(contents=[co_po_prompt], generation_config=step3_generation_config)
                clp_data.update(json.loads(response_co_po.text))
                current_app.logger.info("--- [AI DEBUG] Step 3: CO to PO mapping generated successfully. ---")
            except genai.types.generation_types.BlockedPromptException as e:
                raise ValueError(f"AI generation failed (Blocked): {e.message}")
            except Exception as e:
                raise RuntimeError(f"AI generation failed (Step 3): {e}")

            # --- Step 4: Generate Weekly Breakdown ---
            current_app.logger.info("--- [AI DEBUG] Step 4: Generating Weekly Breakdown... ---")
            weekly_breakdown_prompt = f"""
You are an expert academic planner at the University of La Salette, Inc. for the College of Information Technology.
Generate the complete 18-week Course Outline for the subject: "{subject_name}".

You MUST generate a SINGLE, flat JSON object.
This JSON object MUST contain the following 5 distinct keys for EACH of the 18 weeks (total 90 keys).
Each key corresponds to a specific content type for that week.

For single weeks (Weeks 1-9, 12-13, 18), use keys:
- "Wn_LO" (Learning Outcomes for Week n)
- "Wn_TO" (Topic Outline for Week n)
- "Wn_Method" (Methodology for Week n)
- "Wn_Assesment" (Assessment for Week n)
- "Wn_LR" (Learning Resources for Week n)

For COMBINED weeks, you MUST use these EXACT keys:
- "W1011_LO", "W1011_TO", "W1011_Method", "W1011_Assesment", "W1011_LR"
- "W1415_LO", "W1415_TO", "W1415_Method", "W1415_Assesment", "W1415_LR"
- "W1617_LO", "W1617_TO", "W1617_Method", "W1617_Assesment", "W1617_LR"

Every value associated with these keys MUST be a JSON string.
You MUST use "\\n" for newlines and "• " for list items where appropriate WITHIN the string value.
DO NOT INCLUDE ANY COMMENTS OR NON-JSON TEXT. All fields MUST be filled with realistic and relevant content.

Additionally, at the end of the JSON object, add a key named "references" with a comprehensive list of all references. Format this list as a single string with distinct categories like "Website", "Textbook", "Journal".

**You MUST STRICTLY follow this format for each week's content:**
- The `Wn_LO` value MUST start with "At the end of the week, students should have the ability to:\\n".
- The `Wn_TO` value MUST have the main topic followed by a list of sub-topics, all with bullet points.
- The `Wn_Method` value MUST have a list of bulleted items.
- The `Wn_Assesment` value MUST have a list of bulleted items.
- The `Wn_LR` value MUST have a list of bulleted items, grouped by category like "Textbook", "Website", etc.

**DETAILED EXAMPLE FOR ONE WEEK'S CONTENT:**
- "W1_LO": "At the end of the week, students should have the ability to:\\n• Explain the University of La Salette vision, mission, core values, core competencies, institutional objectives and outcomes;\\n• Relate BSIT program educational outcomes to the institutional outcomes;"
- "W1_TO": "Course Orientation\\n• University’s vision, mission, core values, core competencies, institutional objectives and institutional outcomes\\n• BSIT program description\\n• Course information"
- "W1_Method": "• Interactive discussion\\n• Recitation on the university’s vision, mission, core values, core competencies, institutional objectives and institutional outcomes"
- "W1_Assesment": "• Short quiz about the university policies\\n• Writing a reflective essay on the purpose of institutional outcomes in helping students become what they want to become\\n• Conceptualize a career plan aligned with BSIT program and core values of ULS"
- "W1_LR": "Student Handbook\\nCHED CMO 25, series 2015 “PSG for IT Education”\\nCurriculum Guidelines for Baccalaureate Degree Programs in Information Technology (IT2017) of ACM and IEEE-CS\\nULS Official Website\\nhttps://uls.edu.ph"

Output ONLY a JSON object. Do NOT include any introductory or concluding remarks, explanations, or markdown wrappers.
"""
            weekly_schema_properties = {}
            week_prefixes = [f"W{i}" for i in range(1, 19) if i not in [10, 11, 14, 15, 16, 17]] + ["W1011", "W1415", "W1617"]
            for week_prefix in week_prefixes:
                for suffix in ["_LO", "_TO", "_Method", "_Assesment", "_LR"]:
                    weekly_schema_properties[f"{week_prefix}{suffix}"] = {"type": "STRING"}
            weekly_schema_properties['references'] = {"type": "STRING"}
            step4_generation_config = {"response_mime_type": "application/json", "response_schema": {"type": "OBJECT", "properties": weekly_schema_properties, "required": list(weekly_schema_properties.keys())}}
            
            try:
                response_weekly = model_instance.generate_content(contents=[weekly_breakdown_prompt], generation_config=step4_generation_config)
                clp_data.update(json.loads(response_weekly.text))
                current_app.logger.info("--- [AI DEBUG] Step 4: Weekly Breakdown generated successfully. ---")
            except genai.types.generation_types.BlockedPromptException as e:
                raise ValueError(f"AI generation failed (Blocked): {e.message}")
            except Exception as e:
                raise RuntimeError(f"AI generation failed (Step 4): {e}")
            current_app.logger.info("--- [AI DEBUG] Injecting user-provided course data. ---")
            clp_data.update(course_data)
            

            # --- SUPABASE INTEGRATION START ---
            
            # 1. [SUPABASE] Fetch the user's profile from the 'users' table.
            current_app.logger.info("--- [AI DEBUG] BG Task: Fetching user profile from Supabase... ---")
            user_res = supabase.table('users').select('first_name, last_name, title').eq('id', user_id).single().execute()
            if not user_res.data:
                raise RuntimeError(f"User profile not found for user_id: {user_id}")
            current_user = user_res.data
            
            # 2. Prepare placeholders for the document template.
            author_full_name = f"{current_user.get('first_name', '')} {current_user.get('last_name', '')}".strip()
            author_title = current_user.get('title', '')
            clp_data['NAME'] = author_full_name
            clp_data['TITLE'] = author_title
            
            # 3. [SUPABASE] Insert the initial record into the database.
            final_subject = clp_data.get('descriptive_title', subject_name)
            current_app.logger.info(f"--- [AI DEBUG] BG Task: Inserting initial CLP record for '{final_subject}'... ---")
            inserted_plan_res = supabase.table('course_learning_plans').insert({
                'department': department,
                'subject': final_subject,
                'content': json.dumps(clp_data, indent=2),
                'upload_type': 'ai_generated',
                'status': 'draft',
                'user_id': user_id
            }).execute()
            new_clp = inserted_plan_res.data[0]
            new_clp_id = new_clp['id']
            
            current_app.logger.info(f"--- [AI DEBUG] BG Task: Generating .docx file for CLP ID: {new_clp_id}... ---")
            TEMPLATE_KEY = "PBSIT/PBSIT-001-LP-20242.docx"
            try:
    # Download template bytes from Supabase
                template_bytes = supabase.storage.from_(STORAGE_BUCKET_NAME).download(TEMPLATE_KEY)
                if not template_bytes:
                    raise FileNotFoundError(f"Critical Error: Template '{TEMPLATE_KEY}' not found in Supabase Storage.")
            except Exception as e:
                raise FileNotFoundError(f"Critical Error: Could not fetch template from Supabase. {e}")
            doc = Document(io.BytesIO(template_bytes))

# Replace placeholders
            flat_replacements = flatten_json(clp_data)
            doc = replace_placeholders(doc, flat_replacements)

# Save generated file into memory
            file_stream = io.BytesIO()
            doc.save(file_stream)
            file_stream.seek(0)
            
            # 5. [SUPABASE] Upload the generated .docx file to Supabase Storage.
            docx_filename = f"{new_clp['subject'].replace(' ', '_')}_Generated_{new_clp_id}.docx"
            file_path_in_bucket = f"{user_id}/{docx_filename}"
            
            current_app.logger.info(f"--- [AI DEBUG] BG Task: Uploading '{docx_filename}' to Supabase Storage... ---")
            supabase.storage.from_(STORAGE_BUCKET_NAME).upload(
                path=file_path_in_bucket,
                file=file_stream.read(),
                file_options={"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
            )
            
            # 6. [SUPABASE] Update the database record with the filename and change its status.
            current_app.logger.info(f"--- [AI DEBUG] BG Task: Finalizing database record for CLP ID: {new_clp_id}... ---")
            supabase.table('course_learning_plans').update({
                'filename': file_path_in_bucket,
                'upload_type': 'file_upload',
                'content': None # Clear the JSON content to save space
            }).eq('id', new_clp_id).execute()
            
            # 7. Notify the user of success.
            create_notification(user_id, f'Your AI-generated CLP for "{final_subject}" is ready and saved as a downloadable file.')
            current_app.logger.info("--- [AI DEBUG] BG Task: Process completed successfully. ---")
            
        except FileNotFoundError as e:
            current_app.logger.error(f"--- [AI DEBUG] BG Task FAILED: {e} ---")
            create_notification(user_id, f'CLP generation for "{subject_name}" failed: {e}. Please contact an administrator.')
        except ValueError as e:
            current_app.logger.error(f"--- [AI DEBUG] BG Task FAILED: {e} ---")
            create_notification(user_id, f'CLP generation failed due to AI content policy. Please try a different subject.')
        except RuntimeError as e:
            current_app.logger.error(f"--- [AI DEBUG] BG Task FAILED: {e} ---")
            create_notification(user_id, f'CLP generation failed due to an AI service error. Please try again later.')
        except Exception as e:
            current_app.logger.error(f"--- [AI DEBUG] BG Task FAILED for user {user_id}: {e} ---")
            # Log the full exception for detailed debugging
            current_app.logger.error("Error on line {}: {}".format(sys.exc_info()[-1].tb_lineno, e))
            create_notification(user_id, f'CLP generation for "{subject_name}" failed unexpectedly. An administrator has been notified.')