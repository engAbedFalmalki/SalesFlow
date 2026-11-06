# UNVERIFIED (Docker was not available where this project was built). Python 3.12 + ODBC Driver 18.
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql.list \
 && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY salesflow ./salesflow
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "salesflow", "api"]
