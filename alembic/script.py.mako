${imports}

from models import target_metadata

with_(context, as_="migrate"):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        **${if "%s" in kwargs: literal_binds else: {}},
    )
    with context.begin():
        context.run_migrations()