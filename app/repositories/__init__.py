"""SQLAlchemy data access.

Repositories receive a session and never create one; transaction boundaries
belong to services. Nothing here commits.
"""
