# An OpenID Connect provider for tests and for the demo. Single sign-on cannot
# be exercised without one, and pointing `make dev` at a real provider would
# put somebody else's service on the critical path of a local start-up.
FROM python:3.12-slim

RUN pip install --no-cache-dir "pyjwt[crypto]>=2.8" "cryptography>=43.0"

COPY images/idp-fixture/server.py /server.py

EXPOSE 8082
CMD ["python", "/server.py"]
