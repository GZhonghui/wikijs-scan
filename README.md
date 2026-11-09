# wikijs-scan

面向个人写作规范的 **Wiki.js 3.0.2 数据库扫描工具**，项目目录为 `WikijsCheck/`。
只读取页面原始 Markdown、目录和资源元数据，不导出文件树、不读取媒体内容。
各 scan 独立返回结果，由入口按配置顺序执行并统一写日志。

要求 Python 3.11+、PostgreSQL 18；数据库只有一个站点，内容语言为 `en`，页面全部为 Markdown。
不符合这些前提或目录数据不完整时，程序明确失败，不把未扫描的数据当作正常结果。

## 安装与运行

```bash
cd WikijsCheck
python -m pip install -r requirements.txt
```

编辑本目录的 `config.json`，将 `global.database` 设置为恢复后的数据库连接信息。
示例默认地址为 `127.0.0.1:55432`、数据库 `wiki`、用户 `wikijs`；密码需要填写备份中原有的密码。

```bash
python main.py
```

`main.py` 不接收业务参数，始终读取脚本旁的 `config.json`，从其他工作目录运行也一样。
控制台逐项输出 `scan_id：成功/失败` 和总结果；详细内容写入
`logs/<Unix纳秒时间戳>.txt`。每次运行创建新文件，记录起止时间、耗时、扫描结果和失败原因。
各 scan 自身不打印、不写文件。配置或连接失败也会记录日志；日志目录不可写时直接报错。

退出码：`0` 全部成功；`1` 有规则失败、依赖不满足或违反过滤限制；`2` 配置、连接、I/O 或 scan 运行异常。

## 恢复数据库副本

如果已经有可访问的副本数据库，直接配置连接并扫描即可。恢复是一个独立的显式操作：

```bash
python restore_database.py /path/to/wiki-prod_db-data.tar.gz
```

此脚本接收用户现有备份脚本生成的**停库整卷 tar.gz**，从同一个 `config.json` 读取：

| 配置 | 默认值 / 用途 |
|---|---|
| `global.restore.image` | `postgres:18.6-trixie`，与源数据库相同的镜像 |
| `platform` | `linux/amd64`，与源数据库相同架构 |
| `volume_name` | `wiki-prod_db-data` |
| `container_name` | `wikijs-scan-db` |
| `bind_host` | `127.0.0.1`，宿主机端口绑定地址 |
| `host_port` | `55432`，须与 `global.database.port` 相同 |
| `ready_timeout_seconds` | `60`，启动等待时限 |

脚本校验归档、恢复空卷，将卷挂载到 `/var/lib/postgresql`，映射容器的 `5432` 端口，
再通过 `global.database` 中的地址和凭据验证连接。镜像未安装时会下载。只启动数据库，不启动 Wiki.js。

- 归档必须保留 `18/docker/PG_VERSION` 等原始层级、权限及 UID/GID。
- 恢复保留原数据库名、用户名和密码。修改 `POSTGRES_*` 初始化变量不会修改恢复库；本脚本也不修改凭据。
- 目标容器已存在、卷被容器使用或卷非空时，拒绝覆盖。
- 仅支持普通 local Docker 卷与普通文件/目录归档；不支持通过链接安排的外部 tablespace/WAL。
- 恢复失败时保留已创建的数据，便于排查，不自动删除或清空卷。
- 如果需要从其他机器连接，将 `bind_host` 设置为目标宿主机的绑定地址，并让 `database.host` 指向可连接的宿主机地址。

备份格式依据：[现有备份脚本](https://raw.githubusercontent.com/GZhonghui/Tools/refs/heads/main/Wikijs/backup_wikijs_volume.sh)。

## 配置组织

配置分为 `global` 与 `scans`。完整默认配置见 [config.json](config.json)。

`global` 包含数据库连接、恢复参数、`scan_order`、`IMAGES_DIR`、`DOCS_DIR` 和 `external_prefixes`。
目录设置是**单级名称**，不带 `/`，默认分别为 `images`、`docs`。
数据库连接支持 `host`、`port`、`dbname`、`user`、`password`、`sslmode`、`connect_timeout`（秒）和
`statement_timeout_ms`（每条查询的超时毫秒数）。

每个 scan 的配置形状相同，例如：

```json
{
  "enabled": true,
  "depends_on": ["reference_format"],
  "filter": {
    "enabled": false,
    "prefixes": ["/draft/"]
  },
  "options": {}
}
```

- `scan_order` 决定顺序，`enabled` 决定是否执行；未进入顺序列表的项不执行。
- `depends_on` 是本项执行前必须成功完成的 scan ID。默认依赖见下表。
- 不自动排序、不检查依赖环。前置禁用、失败或尚未执行，本项记失败并跳过；其他项继续。
- 各配置、选项和正则会先验证。未知字段、重复 JSON 键、错误类型会报错。
- 不同 scan 的 options 独立；引用的公共路径规则放在 global，保证前后项解释一致。

## 十二个 scan case

| ID | 规则与失败条件 | 允许过滤 | 默认前置 |
|---|---|---|---|
| `inventory` | 统计目录、空目录、各后缀数量；空目录或无后缀文件失败 | 否 | — |
| `resource_location` | 图片必须直接在 images 下，其他资源直接在 docs 下 | 是 | — |
| `resource_size` | 资源超过 `max_bytes` 失败，默认 1,048,576 字节（1 MiB） | 否 | — |
| `duplicate_sizes` | 多个资源具有完全相同字节数时失败，列出各组 | 否 | — |
| `naming` | 页面/目录 title 等于末级名称，且名称只含 `[a-z0-9-]+` | 是 | — |
| `forbidden_patterns` | 原始页面全文命中配置中的正则即失败 | 否 | — |
| `reference_format` | 引用格式错误、block 相对路径、自引用或 block 解析失败 | 否 | `inventory` |
| `reference_targets` | 引用目标在当前视图中不存在即失败 | 是 | `reference_format` |
| `orphan_files` | 任意页面或资源入链数为零即失败 | 是 | `reference_format` |
| `connectivity` | 所有文件不属于恰好一个弱连通分量即失败 | 是 | `reference_format` |
| `folder_index` | 文件夹缺少同路径页面即失败，末级 images/docs 除外 | 是 | — |
| `resource_folders` | images/docs 重复、相互嵌套，或其下存在子文件夹时失败 | 否 | — |

禁用过滤的集合定义在 `main.py`。对这些项打开 `filter.enabled`，该项会失败并记录配置原因，后续继续。

选项：

- `resource_location.options.image_extensions`：默认 `png, svg, jpg, jpeg, gif, webp`；不带点，忽略扩展名大小写。
- `resource_size.options.max_bytes`：正整数字节数；等于阈值允许。资源缺少有效大小时明确失败。
- `forbidden_patterns.options.patterns`：Python 正则字符串数组，默认 `DOKU2MD_REVIEW` 与 `\[\[[\s\S]*?\]\]`。
  JSON 中反斜杠需双写，示例已在默认配置中。默认大小写敏感，扫描包含代码块和注释的原始全文，报告行列及有限预览。
- 其他项的 `options` 为 `{}`。

相同大小只说明可能重复，扫描按个人规范判失败，并不比较媒体内容。
根目录 `/` 不算文件夹。文件后缀按最后一个点判断，`.tar.gz` 归入 `.gz`。
`/a.md` 是 `/a/` 的同名页面，不是它的内部文件；`/a/` 没有实际子项时仍是空目录。

## 过滤语义

所有过滤默认关闭。开启后使用**区分大小写的纯前缀匹配**，没有 glob 或正则：

- `/home` 会过滤 `/home.md`、`/home/`、`/home/topic.md`、`/homepage.md`。
- `/home/` 会过滤该目录及其内容，不过滤 `/home.md`。
- `*` 没有通配含义；递归排除目录应写 `/home/`，不要写 `/home/*`。

每项从完整只读快照生成自己的视图，互不影响。被过滤的页面、资源、目录相当于不存在，
被过滤页面的出链也消失。保留页面指向被过滤文件时，存在性扫描会失败。
这不是“只免除某个报错”：例如过滤 `/home.md` 也会删除它对其他文件的引用。

## 引用解析规则

只解析四类来源：Markdown `[]()`、`![]()`、`block-gallery` 和 `block-links-grid` 的 `url`。
普通代码块、行内代码、注释、HTML 属性、引用式链接和自动链接不参与提取。
gallery/grid 内的数据代码块必须解析；优先使用首个代码块，支持 fenced 与缩进形式。
grid 使用安全 YAML 解析，非空但格式错误、不是列表或缺少字符串 `url` 都会报错。
空 grid 或空列表表示没有引用。暂不扫描 grid 的其他资源字段。

路径遵循本项目的个人规范：

1. 将 `/_files/` 替换为 `/`。这里只替换字符串前缀。
2. 以 `global.external_prefixes` 中任意字符串开头的引用跳过，默认 `http`、`mailto`。
3. 去掉查询参数和锚点；剩余空地址属于自引用，失败。URL 路径转义在去参数之后解码。
4. 普通链接（`[]()` / grid）无后缀时补 `.md`；图片和 gallery 不补。
5. `/` 从 Wiki 根解析；`./` 从来源页面父目录解析，block 内禁止相对路径。
   裸路径和直接以 `../` 开头的路径不合规范。
6. 归一化之后，目标等于来源页面属于自引用，失败。格式 scan 不查询目标是否存在。

例如 `[下载](/_files/docs/manual)` **按要求匹配 `/docs/manual.md`**。
`![](/_files/images/pic.png)` 匹配 `/images/pic.png`。
`[标题](#section)` 与显式指向自己页面的链接都会失败。

后续三项分别使用自己的视图检查目标、零入链和弱连通：

- 自引用已经由前置项拒绝，外链不计入图。
- 不存在的目标不会生成虚构节点；所有现存页面与资源都作为节点，包括完全没有引用的文件。
- 弱连通忽略边方向，允许环；两个页面引用同一资源也会连接起来。
- 空图有零个连通分量，因此连通性扫描失败；只有一个文件的图则有一个分量。
- 零入链和弱连通是独立规则，后两项不依赖目标存在性扫描成功。

## 数据与边界

数据库适配使用显式列选择，在一个 `READ ONLY / REPEATABLE READ` 事务中加载
`sites`、`pages`、`tree`、`assets`。页面正文来自 `pages.content`，目录由 tree 显式记录构建，
`folderPath` 的每级十六进制编码先还原为 UTF-8。页面路径统一追加逻辑 `.md` 后缀。
上传资源保持 Asset 身份，即使其后缀为 `.md`，也不会读取媒体正文。

该工具判断的是数据库中登记的内容，不验证外部存储中的媒体字节是否仍存在，
也不扫描页面历史、权限可见性或 HTML 编辑器内容。Markdown lint 按方案暂不实现。

实现依据：[数据库 schema](https://github.com/requarks/wiki/blob/3.0.2/backend/db/schema.ts)、
[目录编码](https://github.com/requarks/wiki/blob/3.0.2/backend/helpers/common.ts)、
[gallery](https://github.com/requarks/wiki/blob/3.0.2/blocks/block-gallery/component.js)、
[links-grid](https://github.com/requarks/wiki/blob/3.0.2/blocks/block-links-grid/component.js)。
正文提取参考该版本的渲染行为，路径规范以本项目定义为准。

## 结构与扩展

```text
WikijsCheck/
├── main.py
├── restore_database.py
├── config.json
├── requirements.txt
├── README.md
├── DESIGN.md
├── wikijs_scan/
│   ├── models.py / database.py / filtering.py
│   ├── markdown.py / references.py
│   ├── config.py / registry.py / runner.py
│   ├── restore.py
│   └── scans/                  # 每项一个模块
└── tests/
```

新增 scan：

1. 在 `wikijs_scan/scans/` 新建模块，模块头注释写清范围、流程和成败条件。
2. 实现 `validate_options(options)` 和 `run(context, options) -> ScanResult`。
   使用 `context.snapshot` 与 `context.settings`，只返回 `ScanResult(success, log_text)`，不执行数据库或输出操作。
3. 在 `registry.py` 注册由小写字母和下划线组成的稳定 ID。
4. 在 `config.json` 添加配置、顺序与依赖。如果规则禁止过滤，将 ID 加入 `main.py` 的集合。
5. 增加能验证规则边界的测试。不要让 scan 互相调用或依赖另一个 scan 的中间状态。

详细设计见 [DESIGN.md](DESIGN.md)。旧 `wikijs_check` 包和磁盘目录 CLI 已移除。

## 验证

常规测试不连接 Docker 或真实数据库：

```bash
python -m unittest discover -s tests
```

可选的真实 PostgreSQL 验证：

```bash
python tests/integration_postgres.py
```

此测试只创建随机命名的测试容器和卷，使用官方 PG18 镜像，验证真实 SQL、
禁止读取媒体列的账号权限、全部 scan、停库备份与恢复后的快照一致性。
结束后清理本次测试资源；不使用 `config.json` 中的数据库连接，不接触已有数据库。
