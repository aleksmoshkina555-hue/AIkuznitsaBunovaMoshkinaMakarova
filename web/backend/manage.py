#!/usr/bin/env python
"""Управление веб-версией ИИ-консультанта (Django): runserver, migrate, import_legacy, test."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "konsultant.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
