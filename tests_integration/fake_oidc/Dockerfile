# Build context: repo root.
FROM python:3.12-slim
RUN pip install --no-cache-dir starlette uvicorn authlib
WORKDIR /app
COPY tests/support/ tests/support/
RUN touch tests/__init__.py
ENV FAKE_OIDC_ISSUER=http://fake-oidc:9000
EXPOSE 9000
CMD ["uvicorn", "tests.support.fake_oidc:app", "--host", "0.0.0.0", "--port", "9000"]
