import os
import google.generativeai as genai
from flask import (Flask, render_template, request, redirect, flash, jsonify, Response, current_app)
from supabase import create_client, Client, PostgrestAPIError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

# Initialize supabase and limiter at the top level
supabase: Client = None
limiter: Limiter = None

# --- STATIC DATA & CONFIGURATION ---
PROGRAM_OUTCOMES = [
    {"code": "IT01", "description": "Apply knowledge of computing, science and mathematics appropriate to the discipline"},
    {"code": "IT02", "description": "Understand best practices and standards and their applications"},
    {"code": "IT03", "description": "Analyze complex problems, and identify and define the computing requirements appropriate to its solution"},
    {"code": "IT04", "description": "Identify and analyze user needs and take them into account in the selection, creation, evaluation and administration of computer-based systems"},
    {"code": "IT05", "description": "Design, implement and evaluate computer-based systems, processes, components or programs to meet desired needs and requirements under various constraints"},
    {"code": "IT06", "description": "Integrate IT-based solutions into the user environment effectively"},
    {"code": "IT07", "description": "Apply knowledge through the use of current techniques, skills, tools and practices necessary for the IT profession"},
    {"code": "IT08", "description": "Function effectively as a member or leader of a development team recognizing the different roles within a team to accomplish a common goal"},
    {"code": "IT09", "description": "Assist in the creation of an effective IT project plan"},
    {"code": "IT10", "description": "Communicate effectively with the computing community and with society at large about complex computing activities through logical writing, presentations and clear instructions"},
    {"code": "IT11", "description": "Analyze the local and global impact of computing information technology on individuals, organizations and society"},
    {"code": "IT12", "description": "Understand professional, ethical, legal, security and social issues and responsibilities in the utilization of information technology."},
    {"code": "IT13", "description": "Recognize the need for and engage in planning self-learning and improving performance as a foundation for continuing professional development"}
]
COURSE_OUTCOMES = [
    {"code": "L012", "description": "Analyze different user populations with regard to their abilities and characteristics for using both software and hardware products, and Evaluate the design of existing user interfaces based on the cognitive models of target user"},
]
INSTITUTIONAL_OUTCOMES_HEADERS = ['T', 'R1', 'I1', 'R2', 'I2', 'C', 'H']
PROGRAM_OUTCOMES_HEADERS = ['IT01', 'IT02', 'IT03', 'IT04', 'IT05', 'IT06', 'IT07', 'IT08', 'IT09', 'IT10', 'IT11', 'IT12', 'IT13']
STORAGE_BUCKET_NAME = 'clp_files'
ALLOWED_EXTENSIONS = {'docx', 'pdf'}

def create_app():
    
    global supabase, limiter
    
    app = Flask(__name__)

    # Load environment variables from .env file
    load_dotenv()
    
    # SECURITY: Use environment variables for all sensitive keys.
    app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY')
    
    # --- FIX START: Set keys to app.config so they can be accessed from blueprints ---
    app.config['GEMINI_API_KEY'] = os.environ.get('GEMINI_API_KEY')
    app.config['SUPABASE_URL'] = os.environ.get('SUPABASE_URL')
    app.config['SUPABASE_KEY'] = os.environ.get('SUPABASE_KEY')
    
    # Check for missing environment variables at startup.
    if not all([app.config['SECRET_KEY'], app.config['GEMINI_API_KEY'], app.config['SUPABASE_URL'], app.config['SUPABASE_KEY']]):
        raise ValueError("FATAL ERROR: One or more required environment variables are not set. "
                         "Please set FLASK_SECRET_KEY, GEMINI_API_KEY, SUPABASE_URL, and SUPABASE_KEY.")
    
    # Configure Gemini AI
    genai.configure(api_key=app.config['GEMINI_API_KEY'])
    
    # Supabase Client Initialization
    supabase = create_client(app.config['SUPABASE_URL'], app.config['SUPABASE_KEY'])  # anon key
    supabase_service = create_client(app.config['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])  # service key
    app.config['SUPABASE_SERVICE'] = supabase_service
    
    # Rate Limiter Configuration
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["200 per day", "50 per hour"],
        storage_uri="memory://",
    )
    
    # Pass static data to the Jinja templates
    app.config['PROGRAM_OUTCOMES'] = PROGRAM_OUTCOMES
    app.config['COURSE_OUTCOMES'] = COURSE_OUTCOMES
    app.config['INSTITUTIONAL_OUTCOMES_HEADERS'] = INSTITUTIONAL_OUTCOMES_HEADERS
    app.config['PROGRAM_OUTCOMES_HEADERS'] = PROGRAM_OUTCOMES_HEADERS
    app.config['STORAGE_BUCKET_NAME'] = STORAGE_BUCKET_NAME
    app.config['ALLOWED_EXTENSIONS'] = ALLOWED_EXTENSIONS

    # --- Register Blueprints ---
    from .blueprints.auth import auth_bp
    from .blueprints.main import main_bp
    from .blueprints.admin import admin_bp
    from .blueprints.dean import dean_bp
    from .blueprints.teacher import teacher_bp
    
    app.register_blueprint(auth_bp, url_prefix='/')
    app.register_blueprint(main_bp, url_prefix='/')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(dean_bp, url_prefix='/dean')
    app.register_blueprint(teacher_bp, url_prefix='/teacher')

    # --- CONSOLIDATED SECURITY HEADERS & CSP ---
    @app.after_request
    def add_security_headers(response):
        # Cache control headers
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        
        # Security headers
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        
        # Comprehensive Content Security Policy
        # This CSP includes ONLYOFFICE support while maintaining security
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
                "https://cdn.tailwindcss.com "
                "https://cdn.tiny.cloud "
                "http://localhost:8080 "
                "http://127.0.0.1:8080; "
            "style-src 'self' 'unsafe-inline' "
                "https://cdn.tailwindcss.com "
                "https://fonts.googleapis.com "
                "https://cdn.tiny.cloud "
                "http://localhost:8080 "
                "http://127.0.0.1:8080; "
            "font-src 'self' data: "
                "https://fonts.gstatic.com "
                "https://cdn.tiny.cloud "
                "http://localhost:8080 "
                "http://127.0.0.1:8080; "
            "img-src 'self' data: blob: "
                "https://cdn.tiny.cloud "
                "http://localhost:8080 "
                "http://127.0.0.1:8080; "
            "connect-src 'self' "
                "https://generativelanguage.googleapis.com "
                f"{app.config['SUPABASE_URL']} "
                "https://cdn.tiny.cloud "
                "http://localhost:8080 "
                "http://127.0.0.1:8080 "
                "http://host.docker.internal:5000 "
                "http://172.17.0.1:5000 "
                "ws://localhost:8080 "
                "ws://127.0.0.1:8080; "
            "frame-src 'self' "
                "http://localhost:8080 "
                "http://127.0.0.1:8080; "
            "worker-src 'self' blob: "
                "http://localhost:8080 "
                "http://127.0.0.1:8080; "
            "child-src 'self' blob: "
                "http://localhost:8080 "
                "http://127.0.0.1:8080; "
            "object-src 'none'; "
            "frame-ancestors 'self'; "
            "form-action 'self'; "
            "base-uri 'self';"
        )
        response.headers['Content-Security-Policy'] = csp
        
        return response

    # --- Error Handlers ---
    @app.errorhandler(403)
    def forbidden(e):
        return render_template('403.html'), 403

    @app.errorhandler(404)
    def not_found_error(e):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def internal_server_error(e):
        app.logger.error(f"Internal Server Error: {e}")
        return render_template('500.html'), 500

    @app.errorhandler(429)
    def ratelimit_handler(e):
        flash("You have exceeded the rate limit. Please try again later.", "warning")
        return redirect(request.referrer or url_for('main.dashboard'))

    return app