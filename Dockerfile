ARG PYTHON_VERSION=python:3.12.9-slim
FROM ${PYTHON_VERSION}

WORKDIR /www

RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple uv supervisor

COPY ./src /www
COPY ./src/.env.example /www/.env
COPY ./bin/supervisord/supervisord.conf /etc/supervisor/supervisord.conf
COPY ./bin/supervisord/conf.d /etc/supervisor/conf.d
COPY ./bin/docker-entrypoint.sh /docker-entrypoint.sh

RUN uv sync

EXPOSE 8080

RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
