# GMAPS agent (Open Agent Hackathon 2026). Stdlib only: no packages install at build or run time.
#   docker build -t gmaps .
#   docker run --rm -p 8000:8000 -e SAMPLE_MODE=true gmaps                     # judges: no credentials, no Nightshift
#   docker run --rm -p 8000:8000 --add-host=host.docker.internal:host-gateway \
#     -e NIGHTSHIFT_BASE_URL=http://host.docker.internal:8088/v1 -e NIGHTSHIFT_API_KEY=... gmaps   # live via Nightshift on the host
#   curl -X POST localhost:8000/run -H 'Content-Type: application/json' -d @input_examples/example_1.json
FROM python:3.12-slim
WORKDIR /app
COPY . /app
ENV GMAPS_HOST=0.0.0.0 PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python3", "run.py", "--serve"]
