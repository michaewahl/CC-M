FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
RUN uv pip install --system -e .

COPY ccm/ ./ccm/

EXPOSE 8082
CMD ["uvicorn", "ccm.main:app", "--host", "0.0.0.0", "--port", "8082"]
