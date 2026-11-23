# ProductGrapher（How2Trust）

How2Trust 把本地 Python 仓库画成可以逐层展开的架构图。先按源码识别入口、模块、类和方法调用，再由你选择的 LLM 解释产品提供哪些能力、每个入口把事情交给谁、会读写哪些业务对象。图上的代码关系、模型讲解和批注都关联到同一份源码快照。

**先看一个成品。** 打开[多仓订单履约架构图](audit/fulfillment-deepseek-improved/architecture.html)，在首页查找补货、取消和退货共同调用的 `Desk.retry`，展开可定位到三个入口各自的调用行。也可以搜索「部分发货」，沿 `ship` 入口进入方法调用链，核对订单何时变为 `partial` 或 `shipped`。这份讲解由工作台调用 DeepSeek 生成并复核，图和源码可离线打开。

**换成自己的仓库。** 在 Windows 把项目文件夹拖到 `How2Trust.cmd`，在工作台右上角点「模型设置」，填好地址、模型名与鉴权方式后点「保存并应用」。切到「生产业务」点「一键生成业务图」，或圈选节点后到「问答」提问。也可以从命令行打开：

```powershell
.\How2Trust.cmd serve "D:\projects\my-app" --port 0 --open
```

[从源码仓库生成架构图 · 图文操作说明](docs/从源码仓库生成架构图.html)

可以直接打开两个开源项目的离线示例。在 [Cookiecutter](examples/cookiecutter-2.6.0/workbench.html#view=collaboration) 中展开「项目生成编排」查看嵌套模板的递归调用。在 [python-dotenv](examples/python-dotenv-1.1.1/workbench.html) 中搜索 `load_dotenv`，查看读取配置时何时保留已有环境变量。

## 运行

Windows 用户可双击 `How2Trust.cmd` 打开示例，或把自己的仓库目录拖到这个文件上。启动器会在默认浏览器中打开工作台。关闭服务窗口即可停止。在命令行运行 `.\How2Trust.cmd --help` 查看所有子命令。

使用 Python 3.10 及以上版本，也可在此项目根目录运行：

```powershell
python -m how2trust serve examples/shop --open
python -m how2trust serve "D:\projects\my-app" --port 8765
```

服务只监听本机，默认地址为 `http://127.0.0.1:8765/`。每次启动会分析当前源码，重新启动即可读取改动。需要安装为命令行工具时，可用 `python -m pip install .`，安装后将示例命令里的 `python -m how2trust` 换成 `how2trust`。

## 从产品入口下钻到源码

点击图标题右侧的「全屏查看」可用整屏读图。用滚轮缩放，拖动空白处平移。点调用箭头可在检查器核对源码，点「检查器」可收起面板。按 Esc 或点「退出全屏」回到工作台。导出的 HTML 也支持这些操作。

读长段讲解、问答或源码时，把检查器左边缘的分隔条向左拖宽，向右拖窄。普通视图和全屏共用这个宽度，刷新后仍会沿用。双击分隔条可恢复默认宽度。

1. 打开示例后，依次点击「交易服务」「订单管理」「取消订单」和 DELETE 入口。产品树中的业务名来自示例仓库根目录的 `how2trust.ini`。选中分组可核对配置，选中入口可核对路由声明。
2. 切换到「方法调用链」。示例取消操作由 `cancel_order` 调用 `OrderService.cancel`，后者调用 `OrderStore.get` 和 `OrderStore.save`。点击调用线可检查调用发生的位置，点击目标方法可检查其定义。
3. 切换到「类关系图」查看相关类、继承和可解析的实例属性关联。「分析记录」列出语法错误及无法确定目标的调用。
4. 用画布的矩形选择工具拖过多个节点，或者按住 Shift 逐个点击。打开「批注」可就当前选区做记录。打开「问答」可向配置的模型提问，继续追问，或重新打开已有会话。
5. 在大图中可从左侧搜索函数或方法，例如 How2Trust 的 `build_report`。选择结果后查看源码和直接调用，再用「展开完整调用图」查看更多层的调用关系。

## 自定义产品层级

不用配置也能分析。工具从支持的 FastAPI、Flask、Click、argparse 子命令和脚本声明中提取入口，并提供按实际目录组织的「源码模块」。例如，对多仓订单样例分析可以从 `ship`、`cancel` 等命令定位到各自的 `add_parser` 声明和分派代码。一般库项目可以直接从源码模块下钻。

需要业务名称时，在**被分析仓库的根目录**添加 UTF-8 编码的 `how2trust.ini`。分组名中的 `/` 划分层级。入口选择器写成“仓库相对路径::处理函数的限定名”。同一处理函数注册多条路由时，它们都会进入该组。用 `files` 可以直接把源码模块分配到某个业务组。

```ini
[project]
name = 订单工作台

[group:交易服务/订单管理/取消订单]
entries = shop/api.py::cancel_order

[group:交易服务/订单存储]
files = shop/storage.py
```

多个选择器每行一个，续行需缩进。未匹配的选择器会出现在分析记录里。一个图元素如被明确分配到两个不同的产品分组，工具会报告冲突。

## 生成和保存业务架构讲解

要从业务词进入源码，可在生成时启用模型解读。下例读取本地供应商配置，为多仓样例生成带中文导读的 HTML。讲解阶段读取索引中的 Python 源码、分组配置、入口和代码关系，也会从仓库根 README 摘录最多 2,000 个字符供模型选择业务用词。可选的 `--requirements` 提供团队术语。选择云端 API 时，这些材料会发往所配置的服务；选择本机地址时由本地模型处理。

```powershell
.\How2Trust.cmd export audit/terra-fulfillment/project --llm-config providers.json --with-semantics --semantics my-guide.json -o my-map.html
.\How2Trust.cmd serve audit/terra-fulfillment/project --llm-config providers.json --semantics my-guide.json --open
```

用一个完整仓库即可生成三种视图，服务模式和导出的 HTML 共用同一份源码快照。

- **整仓结构**展示入口、目录和代码模块。按路径将测试与夹具、示例与文档单列，仍可展开和查看源码。
- **生产业务**中点击「一键生成业务图」。画布上方会显示当前阶段、已完成的源码批数和正在处理的文件。失败时可查看原因、修改模型设置并「重试生成」。命令行加 `--with-semantics` 也会在终端报告阶段和批次。工作台按生产源码和静态调用归并业务职责，点击总览结论或模块成员可以核对行号。
- **模块协作**按模块汇总已解析的调用和入口绑定。有业务讲解时使用业务分组，其他实现按源码模块展示。选择「它调用谁」或「谁调用它」，点箭头查看全部调用点、所在分支、提前返回等可定位的路径条件。每个条件与调用位置都可打开源码。

在「模块协作」画布的默认手形工具（拖动画布）下，单击模块卡片可进入模块内部关系图。点成员查看其声明，点连线查看关系发生的源码行。切换「类关系图」或「方法调用链」可聚焦当前模块的类或调用，点「返回模块详情」回到内部图。在内部图点「返回模块协作」，即可恢复下钻前的模块、方向和关系页。矩形圈选工具仅改变选区，适合选定对象后批注或问答。

总览从带引用的候选结论中选择。对含入口或分支逻辑的源码批次，工作台提供实际调用处及上下游源码让模型复核条件和例外。多批、多文件的总览另按相关函数和调用位置复读，检查提前返回、异常捕获范围及不同输入的默认行为。每条最终结论保留源码引用。若生成中断，可重新执行原命令，复用已校验的批次。业务视图显示得到讲解的生产节点数。

「多个入口共用的实现」展示静态图中能定位的一至两步调用。展开某个共享方法，可分别从每个入口点到真实调用行，查看调用发生的条件。[多仓项目 · 新版 DeepSeek 交互图](audit/fulfillment-deepseek-improved/architecture.html)

讲解保存在单独的 JSON，可随相符的静态快照重放。省略 `--semantics` 时，生成命令会使用本机缓存。要为同一项目保留另一版模型解读，可指定新的讲解文件。

## 接入本地或云端模型

本地工作台的「模型设置」可以同时设置业务解读和图上问答，也可以在「应用范围」分别设置。选择 DeepSeek 或 Ollama 可带入常用地址，模型名可以修改。已有环境变量的用户填写变量名，也可以粘贴仅在本次服务有效的 API Key。本地免鉴权服务选「无需密钥」。Windows 上普通双击或拖入仓库会沿用 `%LOCALAPPDATA%\how2trust\workbench-models.json` 的设置。启动命令显式指定的模型选项或供应商配置文件优先。页面切换解读模型后，可以点「用当前模型重新生成」更新已有业务图。

业务讲解和图上问答可分别指定供应商、API 地址、模型和密钥环境变量。把以下内容保存为 `providers.json`，将模型名替换成自己服务上的名称。可以只保留一个供应商并设置 `default`，也可按阶段覆盖。配置只填写密钥的环境变量名。

```json
{
  "providers": {
    "本地": {"protocol":"openai","base_url":"http://127.0.0.1:11434/v1","model":"LOCAL_MODEL"},
    "云端": {"protocol":"openai","base_url":"https://api.deepseek.com","model":"CLOUD_MODEL","api_key_env":"DEEPSEEK_API_KEY"},
    "Claude": {"protocol":"anthropic","base_url":"https://api.anthropic.com/v1","model":"CLAUDE_MODEL","api_key_env":"ANTHROPIC_API_KEY"}
  },
  "default": "本地",
  "stages": {"semantics":"本地","chat":"云端"}
}
```

`openai` 表示 Chat Completions 兼容协议，`anthropic` 表示原生 Messages 协议。基础 URL 截止在 `/chat/completions` 或 `/messages` 前。配置可按供应商设置 `timeout`（秒）和 `max_tokens`。工作台对 DeepSeek 官方地址的业务讲解使用结构化 JSON 输出；其他兼容供应商支持该能力时可设置 `"json_mode": true`。设置 `HOW2TRUST_LLM_CONFIG` 可为双击启动和命令行共用一份配置。单供应商也可以直接指定命令行参数。

```powershell
# 本地服务示例，将 MODEL_NAME 替换为本地已加载模型的名称
python -m how2trust serve examples/shop --base-url http://127.0.0.1:11434/v1 --model MODEL_NAME

# 云端服务示例，使用该服务的实际 API 基础地址和模型名称
python -m how2trust serve examples/shop --base-url https://YOUR-PROVIDER/v1 --model MODEL_NAME --key-env MY_MODEL_KEY
```

可通过 `HOW2TRUST_BASE_URL` 和 `HOW2TRUST_MODEL` 指定默认值。云端鉴权从启动进程的环境变量读取，默认变量名为 `HOW2TRUST_API_KEY`。选择云端服务后，提问会向该服务发送问题、选区内容、相关历史和按需读取的源码。

模型可检索图、分组和旧消息，也可按文件与行号读取同一快照中的源码。除 Python 文件外，快照收录 UTF-8 编码的 JS/TS（含 JSX、TSX、MJS、CJS）、CSS、HTML、Markdown、RST、TXT、JSON、TOML、YAML 和 INI。附加文本只用作引用材料，跳过隐藏路径、依赖和构建目录、符号链接，以及本工具导出的图和验收报告。源码引用如 `[shop/orders.py:L11-L13]` 或 `[how2trust/assets/app.js:L1-L2]`，查看器可打开本轮模型实际读过的行。

服务模式的批注和聊天记录存放在本机 SQLite 数据库。Windows 默认使用 `%LOCALAPPDATA%\how2trust\discussions.sqlite3`，其他系统使用用户数据目录。用 `--db PATH` 可切换数据库。记录绑定代码快照，同一快照重新打开可以继续对话。每次请求使用有长度上限的近期上下文，早期内容可按需检索。

## 离线导出和可重复性

```powershell
python -m how2trust export examples/shop -o order-map.html --json order-map.json
```

HTML 是独立文件，内含交互界面、图数据、已经索引的源码与分组配置。可以直接用浏览器打开。离线批注保存在当前浏览器的本地存储，问答使用本地服务模式。命令行导出只包含生成结果，本机数据库中的讨论记录单独保存。分享导出文件时，接收者也能阅读内嵌源码。

同一版本的工具和 Python、相同的已索引文件，生成相同的静态图 JSON 和 HTML 字节。带模型解读的导出还需使用同一份已保存讲解才能逐字节复现。重新请求模型可能产生不同措辞。模型解读有独立指纹，不改变静态图的节点、边和快照标识。图中的边记录 `source`、`target`、`kind`、文件及行号，调用边还记录调用表达式。

## 源码证据的范围

- 识别 Python 模块、类、函数、方法、可解析的本地单继承链、直接实例属性赋值及静态调用点。遇到自定义 `__new__` 时，只在能确认返回目标时连出后续方法。复杂工厂和多继承分派仍可能缺边。
- 对 Click 项目，命令组与子命令可追到装饰器声明。工作台也会识别 `setup.py` 中指向本仓库函数的字面量 `setuptools.setup(..., entry_points={"console_scripts": [...]})` 注册，并与 `__main__` 脚本入口分别展示。
- 推导式第一个 `for` 的可迭代表达式可解析到外层的本地函数；对使用标准库 `@contextmanager` 的函数，调用线会注明上下文管理器的工厂调用。两者均保留实际调用行。
- argparse 入口可从字面量子命令名追到注册行、明确条件分支内的调用或可绑定的回调。当必选子命令集合完整、前面的分支逐个排除且最后只剩一个命令时，也可以定位到末尾的 `else`。How2Trust 自己的 `serve` 有一部分启动逻辑位于其他分支提前返回后的函数末尾，当前方法图只覆盖能定位到该命令的显式分支。
- 路由提取覆盖受支持的装饰器和可解析的声明，脚本入口覆盖可解析的 `__main__` 调用。挂载前缀、框架依赖注入和运行时注册等机制会影响实际路由或执行目标。
- 对无法静态解析的动态分派、反射、外部依赖和复杂装饰器保留分析边界。语法错误记录文件位置。分析不执行仓库代码，只读入索引范围内的文件。

## 验证

```powershell
python -m unittest discover -s tests -v
```

测试覆盖证据定位、静态解析边界、跨目录的确定性导出、HTML 源码嵌入、批注持久化以及通过本地模拟模型服务完成的多轮检索问答。

已有交付对比报告或检查清单的用户，可查看[交付审阅命令与旧报告操作说明](docs/交付审阅.md)。
