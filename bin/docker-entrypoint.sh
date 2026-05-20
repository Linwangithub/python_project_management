#!/bin/sh

# tail -f /dev/null
supervisord -n -c /etc/supervisor/supervisord.conf
