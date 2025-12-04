installation
pip install fastapi uvicorn pyyaml


front
python -m http.server 5500

backend 
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000