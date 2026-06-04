FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY temp/caen_rev3_coduri_clase.csv temp/
COPY temp/siruta_toate.csv temp/
COPY temp/siruta_cu_diacritice.csv temp/
COPY temp/exchange_rates/ temp/exchange_rates/
COPY scripts/ scripts/
COPY init_db.py .
COPY main.py .
COPY auth.py .
COPY manage_keys.py .
COPY routers/ routers/

# Initializeaza baza de date la build (CAEN + SIRUTA + cursuri valutare BNR)
RUN python init_db.py

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
