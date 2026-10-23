# 实际第三方依赖（P0 / P1）

版本由 pyproject.toml / uv.lock 精确固定；runtime requirements.txt 由锁导出。POC组不进入进程探针镜像。
许可证下表来自已安装 wheel 的 METADATA/License-Expression 和许可证文件路径（本地可重取）。新增 P1 依赖时重新核实，不把未引入包当已安装。

| 包 | 版本 | 元数据许可证 | wheel内许可证路径 |
|---|---|---|---|
| [annotated-doc](https://pypi.org/project/annotated-doc/0.0.5/) | 0.0.5 | MIT | annotated_doc-0.0.5.dist-info/licenses/LICENSE |
| [annotated-types](https://pypi.org/project/annotated-types/0.8.0/) | 0.8.0 | MIT | annotated_types-0.8.0.dist-info/licenses/LICENSE |
| [anyio](https://pypi.org/project/anyio/4.15.1/) | 4.15.1 | MIT | anyio-4.15.1.dist-info/licenses/LICENSE |
| [attrs](https://pypi.org/project/attrs/26.1.0/) | 26.1.0 | MIT | attrs-26.1.0.dist-info/licenses/LICENSE |
| [certifi](https://pypi.org/project/certifi/2026.10.19/) | 2026.10.19 | MPL-2.0 | certifi-2026.10.19.dist-info/licenses/LICENSE |
| [cffi](https://pypi.org/project/cffi/2.1.1/) | 2.1.1 | MIT-0 | cffi-2.1.1.dist-info/licenses/LICENSE |
| [charset-normalizer](https://pypi.org/project/charset-normalizer/3.5.2/) | 3.5.2 | MIT | charset_normalizer-3.5.2.dist-info/licenses/LICENSE |
| [click](https://pypi.org/project/click/8.5.0/) | 8.5.0 | BSD-3-Clause | click-8.5.0.dist-info/licenses/LICENSE.txt |
| [cryptography](https://pypi.org/project/cryptography/50.0.2/) | 50.0.2 | Apache-2.0 OR BSD-3-Clause | cryptography-50.0.2.dist-info/licenses/LICENSE; cryptography-50.0.2.dist-info/licenses/LICENSE.APACHE |
| [distro](https://pypi.org/project/distro/1.9.0/) | 1.9.0 | Apache License, Version 2.0 | distro-1.9.0.dist-info/LICENSE |
| [fastapi](https://pypi.org/project/fastapi/0.161.1/) | 0.161.1 | MIT | fastapi-0.161.1.dist-info/licenses/LICENSE |
| [h11](https://pypi.org/project/h11/0.16.0/) | 0.16.0 | MIT | h11-0.16.0.dist-info/licenses/LICENSE.txt |
| [httpcore](https://pypi.org/project/httpcore/1.0.9/) | 1.0.9 | BSD-3-Clause | httpcore-1.0.9.dist-info/licenses/LICENSE.md |
| [httpcore2](https://pypi.org/project/httpcore2/2.16.0/) | 2.16.0 | BSD-3-Clause | httpcore2-2.16.0.dist-info/licenses/LICENSE.md |
| [httpx](https://pypi.org/project/httpx/0.28.1/) | 0.28.1 | BSD-3-Clause | httpx-0.28.1.dist-info/licenses/LICENSE.md |
| [httpx2](https://pypi.org/project/httpx2/2.16.0/) | 2.16.0 | BSD-3-Clause | httpx2-2.16.0.dist-info/licenses/LICENSE.md |
| [idna](https://pypi.org/project/idna/3.20/) | 3.20 | BSD-3-Clause | idna-3.20.dist-info/licenses/LICENSE.md |
| [iniconfig](https://pypi.org/project/iniconfig/2.3.1/) | 2.3.1 | MIT | iniconfig-2.3.1.dist-info/licenses/LICENSE |
| [jiter](https://pypi.org/project/jiter/0.17.0/) | 0.17.0 | MIT | jiter-0.17.0.dist-info/licenses/LICENSE |
| [jsonpatch](https://pypi.org/project/jsonpatch/1.36/) | 1.36 | BSD-3-Clause | jsonpatch-1.36.dist-info/licenses/AUTHORS; jsonpatch-1.36.dist-info/licenses/LICENSE |
| [jsonpointer](https://pypi.org/project/jsonpointer/3.4/) | 3.4 | Modified BSD License | jsonpointer-3.4.dist-info/licenses/AUTHORS; jsonpointer-3.4.dist-info/licenses/LICENSE.txt |
| [jsonschema](https://pypi.org/project/jsonschema/4.25.1/) | 4.25.1 | MIT | jsonschema-4.25.1.dist-info/licenses/COPYING |
| [jsonschema-specifications](https://pypi.org/project/jsonschema-specifications/2025.9.1/) | 2025.9.1 | MIT | jsonschema_specifications-2025.9.1.dist-info/licenses/COPYING |
| [langchain-core](https://pypi.org/project/langchain-core/1.7.0/) | 1.7.0 | MIT | langchain_core-1.7.0.dist-info/licenses/LICENSE |
| [langchain-deepseek](https://pypi.org/project/langchain-deepseek/1.1.1/) | 1.1.1 | MIT | langchain_deepseek-1.1.1.dist-info/licenses/LICENSE |
| [langchain-openai](https://pypi.org/project/langchain-openai/1.7.2/) | 1.7.2 | MIT | langchain_openai-1.7.2.dist-info/licenses/LICENSE |
| [langchain-protocol](https://pypi.org/project/langchain-protocol/0.0.20/) | 0.0.20 | MIT | langchain_protocol-0.0.20.dist-info/licenses/LICENSE |
| [langgraph](https://pypi.org/project/langgraph/1.2.16/) | 1.2.16 | MIT | langgraph-1.2.16.dist-info/licenses/LICENSE |
| [langgraph-checkpoint](https://pypi.org/project/langgraph-checkpoint/4.3.0/) | 4.3.0 | MIT | langgraph_checkpoint-4.3.0.dist-info/licenses/LICENSE |
| [langgraph-checkpoint-postgres](https://pypi.org/project/langgraph-checkpoint-postgres/3.2.0/) | 3.2.0 | MIT | langgraph_checkpoint_postgres-3.2.0.dist-info/licenses/LICENSE |
| [langgraph-prebuilt](https://pypi.org/project/langgraph-prebuilt/1.1.1/) | 1.1.1 | MIT | langgraph_prebuilt-1.1.1.dist-info/licenses/LICENSE |
| [langgraph-sdk](https://pypi.org/project/langgraph-sdk/0.4.8/) | 0.4.8 | MIT | langgraph_sdk-0.4.8.dist-info/licenses/LICENSE |
| [langsmith](https://pypi.org/project/langsmith/0.14.12/) | 0.14.12 | MIT | langsmith-0.14.12.dist-info/licenses/LICENSE |
| [openai](https://pypi.org/project/openai/3.37.0/) | 3.37.0 | Apache-2.0 | openai-3.37.0.dist-info/licenses/LICENSE; openai/_vendor/httpx_aiohttp/LICENSE |
| [opentelemetry-api](https://pypi.org/project/opentelemetry-api/1.45.1/) | 1.45.1 | Apache-2.0 | opentelemetry_api-1.45.1.dist-info/licenses/LICENSE |
| [orjson](https://pypi.org/project/orjson/3.13.0/) | 3.13.0 | MPL-2.0 AND (Apache-2.0 OR MIT) | orjson-3.13.0.dist-info/licenses/LICENSE-APACHE; orjson-3.13.0.dist-info/licenses/LICENSE-MIT |
| [ormsgpack](https://pypi.org/project/ormsgpack/1.13.0/) | 1.13.0 | Apache-2.0 OR MIT | ormsgpack-1.13.0.dist-info/licenses/LICENSE-APACHE; ormsgpack-1.13.0.dist-info/licenses/LICENSE-MIT |
| [packaging](https://pypi.org/project/packaging/26.3/) | 26.3 | Apache-2.0 OR BSD-2-Clause | packaging-26.3.dist-info/licenses/LICENSE; packaging-26.3.dist-info/licenses/LICENSE.APACHE |
| [pluggy](https://pypi.org/project/pluggy/1.7.0/) | 1.7.0 | MIT | pluggy-1.7.0.dist-info/licenses/LICENSE |
| [psycopg](https://pypi.org/project/psycopg/3.3.3/) | 3.3.3 | LGPL-3.0-only | psycopg-3.3.3.dist-info/licenses/LICENSE.txt |
| [psycopg-binary](https://pypi.org/project/psycopg-binary/3.3.3/) | 3.3.3 | LGPL-3.0-only | psycopg_binary-3.3.3.dist-info/licenses/LICENSE.txt |
| [psycopg-pool](https://pypi.org/project/psycopg-pool/3.3.3/) | 3.3.3 | LGPL-3.0-only | psycopg_pool-3.3.3.dist-info/licenses/LICENSE.txt |
| [pycparser](https://pypi.org/project/pycparser/3.12/) | 3.12 | BSD-3-Clause | pycparser-3.12.dist-info/licenses/LICENSE |
| [pydantic](https://pypi.org/project/pydantic/2.14.1/) | 2.14.1 | MIT | pydantic-2.14.1.dist-info/licenses/LICENSE |
| [pydantic-core](https://pypi.org/project/pydantic-core/2.50.1/) | 2.50.1 | MIT | pydantic_core-2.50.1.dist-info/licenses/LICENSE |
| [pygments](https://pypi.org/project/pygments/2.21.0/) | 2.21.0 | BSD-2-Clause | pygments-2.21.0.dist-info/licenses/AUTHORS; pygments-2.21.0.dist-info/licenses/LICENSE |
| [pytest](https://pypi.org/project/pytest/9.1.1/) | 9.1.1 | MIT | pytest-9.1.1.dist-info/licenses/LICENSE |
| [pyyaml](https://pypi.org/project/pyyaml/6.0.3/) | 6.0.3 | MIT | pyyaml-6.0.3.dist-info/licenses/LICENSE |
| [referencing](https://pypi.org/project/referencing/0.37.0/) | 0.37.0 | MIT | referencing-0.37.0.dist-info/licenses/COPYING |
| [regex](https://pypi.org/project/regex/2026.9.29/) | 2026.9.29 | Apache-2.0 AND CNRI-Python | regex-2026.9.29.dist-info/licenses/LICENSE.txt |
| [requests](https://pypi.org/project/requests/2.34.2/) | 2.34.2 | Apache-2.0 | requests-2.34.2.dist-info/licenses/LICENSE; requests-2.34.2.dist-info/licenses/NOTICE |
| [requests-toolbelt](https://pypi.org/project/requests-toolbelt/1.0.0/) | 1.0.0 | Apache 2.0 | requests_toolbelt-1.0.0.dist-info/LICENSE |
| [rpds-py](https://pypi.org/project/rpds-py/2026.9.1/) | 2026.9.1 | MIT | rpds_py-2026.9.1.dist-info/licenses/LICENSE |
| [ruff](https://pypi.org/project/ruff/0.14.0/) | 0.14.0 | 未声明，使用前复核原文 | ruff-0.14.0.dist-info/licenses/LICENSE |
| [sniffio](https://pypi.org/project/sniffio/1.3.1/) | 1.3.1 | MIT OR Apache-2.0 | sniffio-1.3.1.dist-info/LICENSE; sniffio-1.3.1.dist-info/LICENSE.APACHE2 |
| [socksio](https://pypi.org/project/socksio/1.0.0/) | 1.0.0 | UNKNOWN | socksio-1.0.0.dist-info/LICENSE |
| [starlette](https://pypi.org/project/starlette/1.9.0/) | 1.9.0 | BSD-3-Clause | starlette-1.9.0.dist-info/licenses/LICENSE.md |
| [tenacity](https://pypi.org/project/tenacity/9.2.1/) | 9.2.1 | Apache-2.0 | tenacity-9.2.1.dist-info/licenses/LICENSE |
| [tiktoken](https://pypi.org/project/tiktoken/0.14.0/) | 0.14.0 | MIT License | tiktoken-0.14.0.dist-info/licenses/LICENSE |
| [truststore](https://pypi.org/project/truststore/0.10.5/) | 0.10.5 | MIT | truststore-0.10.5.dist-info/licenses/LICENSE |
| [typing-extensions](https://pypi.org/project/typing-extensions/4.16.0/) | 4.16.0 | PSF-2.0 | typing_extensions-4.16.0.dist-info/licenses/LICENSE |
| [typing-inspection](https://pypi.org/project/typing-inspection/0.4.4/) | 0.4.4 | MIT | typing_inspection-0.4.4.dist-info/licenses/LICENSE |
| [urllib3](https://pypi.org/project/urllib3/2.8.0/) | 2.8.0 | MIT | urllib3-2.8.0.dist-info/licenses/LICENSE.txt |
| [uuid-utils](https://pypi.org/project/uuid-utils/0.18.0/) | 0.18.0 | BSD-3-Clause | uuid_utils-0.18.0.dist-info/licenses/LICENSE.md |
| [uvicorn](https://pypi.org/project/uvicorn/0.54.0/) | 0.54.0 | BSD-3-Clause | uvicorn-0.54.0.dist-info/licenses/LICENSE.md |
| [websockets](https://pypi.org/project/websockets/16.1.1/) | 16.1.1 | BSD-3-Clause | websockets-16.1.1.dist-info/licenses/LICENSE |
| [xxhash](https://pypi.org/project/xxhash/4.0.2/) | 4.0.2 | BSD-2-Clause | xxhash-4.0.2.dist-info/licenses/LICENSE |
| [zstandard](https://pypi.org/project/zstandard/0.25.0/) | 0.25.0 | BSD-3-Clause | zstandard-0.25.0.dist-info/licenses/LICENSE |

psycopg/psycopg-binary 仅 POC 使用，按其 LGPL 及附带依赖许可证保留授权文本；正式镜像引入时需同步分发 notice。此表不等于对未实现功能作兼容性承诺。

## P1 新增依赖

2026-10-23 从本地 .venv 已安装 wheel 的 METADATA / License-Expression 与 LICENSE 路径核实；版本锁在 pyproject.toml / uv.lock，运行镜像保留 dist-info 授权文件。

| 包 | 精确版本 | 许可证 | wheel 许可证路径 |
|---|---|---|---|
| sqlalchemy | 2.1.6 | MIT | sqlalchemy-2.1.6.dist-info/licenses/AUTHORS; sqlalchemy-2.1.6.dist-info/licenses/LICENSE |
| asyncpg | 0.32.0 | Apache-2.0 | asyncpg-0.32.0.dist-info/licenses/LICENSE |
| alembic | 1.20.1 | MIT | alembic-1.20.1.dist-info/licenses/LICENSE |
| greenlet | 3.5.6 | MIT AND PSF-2.0 | greenlet-3.5.6.dist-info/licenses/LICENSE; greenlet-3.5.6.dist-info/licenses/LICENSE.PSF |
| Mako | 1.4.3 | MIT | mako-1.4.3.dist-info/licenses/LICENSE |
| MarkupSafe | 3.0.4 | BSD-3-Clause | markupsafe-3.0.4.dist-info/licenses/LICENSE.txt |

P1 银行业务代码自研。TigerBeetle 账本原则、Blnk 预演思想为文档参考，未引入其运行时或复制源码；具体映射与来源见 PRD §4。
