ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

RUN apk add --no-cache python3 py3-pip

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --break-system-packages .

COPY run.sh /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
