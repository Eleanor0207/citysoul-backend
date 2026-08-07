"""
Alembic 執行環境（#31）。

跟 `alembic init` 產生的版本有三處刻意的不同：

1. **連線字串從 `app.core.config.settings` 來，不從 `alembic.ini` 來。**
   `alembic.ini` 是會進版控的檔案，資料庫密碼不能寫在那裡。`settings` 讀
   `.env`（gitignored），本機與 Cloud Run 走同一條路徑。

2. **`include_schemas=True`。** 腦袋的表在 `brain` schema，預設的 autogenerate
   只看 `public`，會把 `brain.*` 全部當成「資料庫裡多出來的表」而產生 drop。

3. **`compare_type=True`。** 型別變更（Float → NUMERIC(9,6)）預設不會被偵測到。

autogenerate 產出的東西一律要人看過再套用——它看不出 rename（會產生 drop + add
把資料丟掉），也不會自動處理資料搬移。
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# app 的 metadata。這些 import 有副作用：不 import 到，Base.metadata 就不知道
# 那些表存在，autogenerate 會產生一份「把全部的表都刪掉」的 migration。
from app.core.config import settings
from app.core.database import Base
from app.modules.body import models as body_models  # noqa: F401
from app.modules.brain import models as brain_models  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    # `disable_existing_loggers=False` 不是可有可無的參數。fileConfig 預設會把
    # 呼叫當下已經存在的 logger 全部停用——測試裡 conftest 跑 migration 之後，
    # app 的 logger 就靜音了，反作弊的 caplog 測試會抓到空的日誌而變紅，
    # 而且錯誤訊息完全不會指向這裡。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    """
    PostGIS 與 pgvector 會在資料庫裡建自己的表與索引（`spatial_ref_sys` 等）。
    那些不是我們的 schema，autogenerate 不該把它們當成「該刪掉的多餘物件」。
    """
    if type_ == "table" and name in {"spatial_ref_sys"}:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
