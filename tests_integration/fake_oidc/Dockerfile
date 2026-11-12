# Build context: repo root.
FROM python:3.12-slim
# Pinned to uv.lock so the fake provider signs tokens the way the app's authlib verifies them.
RUN pip install --no-cache-dir starlette==1.10.0 uvicorn==0.54.0 authlib==1.9.5 cryptography==50.0.2 joserfc==1.8.0
WORKDIR /app
COPY tests/support/ tests/support/
RUN touch tests/__init__.py
ENV FAKE_OIDC_ISSUER=http://fake-oidc:9000
EXPOSE 9000
CMD ["uvicorn", "tests.support.fake_oidc:app", "--host", "0.0.0.0", "--port", "9000"]
