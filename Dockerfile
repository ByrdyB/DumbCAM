# Use official Python image from Docker Hub (no GitHub downloads)
FROM python:3.11-slim

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Default port; override at runtime with -e PORT=XXXX if needed
ENV PORT=8080

# Expose port
EXPOSE 8080

# Start gunicorn
CMD gunicorn dumbcam_gui_app:app --bind 0.0.0.0:$PORT
