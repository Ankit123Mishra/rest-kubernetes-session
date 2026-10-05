# Small official Python image
FROM python:3.12-slim

# Don't write .pyc files; flush logs straight to stdout (kubectl logs reads stdout)
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /srv

# Copy requirements first so Docker caches the pip layer between code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Never run as root inside a container
RUN useradd --uid 10001 --no-create-home appuser
USER 10001

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
