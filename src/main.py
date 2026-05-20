#!/usr/bin/python
# -*- coding: utf-8 -*-
# uv run main.py

import argparse
import uvicorn
from app import create_app
from app.core.deps import get_settings

create_app = create_app()


def get_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("args", nargs="*")
    parser.add_argument("-host", "--host", nargs="?", default='0.0.0.0', type=str)
    parser.add_argument("-port", "--port", nargs="?", default=8888, type=int)
    return parser.parse_args()


if __name__ == '__main__':
    parser = get_parser()
    uvicorn.run(
        app='main:create_app',
        workers=1,
        host=parser.host, port=parser.port,
        forwarded_allow_ips='*',
        reload=get_settings().dev.environment != 'production',
    )
