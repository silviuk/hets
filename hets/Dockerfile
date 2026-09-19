ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8

WORKDIR /app
COPY sync.py /app/sync.py
RUN chmod +x /app/sync.py

CMD [ "python3", "/app/sync.py" ]
