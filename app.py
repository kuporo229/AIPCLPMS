# --- IMPORTS ---
import os
from dotenv import load_dotenv

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    # Load environment variables from a .env file
    load_dotenv()
    
    # Check for missing environment variables at startup.
    required_env_vars = ['FLASK_SECRET_KEY', 'GEMINI_API_KEY', 'SUPABASE_URL', 'SUPABASE_KEY']
    for var in required_env_vars:
        if not os.environ.get(var):
            raise ValueError(f"FATAL ERROR: The required environment variable '{var}' is not set.")
    
    # Import the application factory function
    from app import create_app
    
    # Create the Flask application instance
    app = create_app()
    
    # Run the application
    print("Starting Flask application...")
    app.run(debug=True)