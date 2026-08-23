# 无效检索数据源验证记录

日期：2026-07-19
状态：`GO（带约束）`

## 1. 验证目的

在编写无效检索业务代码前，确认至少存在一条可以实际检索并取得全球专利文件的路径、一条可以实际检索并取得非专利文件的路径，以及一条人工导入路径。搜索摘要、题录或模型转述都不能直接进入 Claim Chart；只有取得真实内容、可核验公开时间、稳定标识和来源链后才能升级为合格证据。

## 2. 实测结论

| 来源 | 发现能力 | 真实内容 | 日期与标识 | MVP 决定 |
| --- | --- | --- | --- | --- |
| Google Patents | 全球专利关键词、分类、引证和专利族检索 | HTML 全文、权利要求、附图链接和 PDF | 公开日、申请日、优先权日、公开编号；Google 自身提示优先权日不是法律结论 | `GO`，作为首个实时专利 provider；所有日期由日期引擎二次判定，网页摘要不作为证据 |
| EPO OPS | DOCDB 全球题录、家族、引证、部分全文和图像 | REST/XML/PDF | 公开编号和规范日期字段 | `READY_WITH_CREDENTIALS`；接口保留，取得 OAuth 凭据后启用 |
| WIPO PATENTSCOPE | PCT 及参与局馆藏、NPL 入口 | 网页全文/文件，机器接口能力依具体馆藏 | 公开编号和日期 | `DISCOVERY_ONLY`；不依赖未验证的私有接口 |
| arXiv | 论文标题、摘要和日期过滤 | Atom 元数据和公开 PDF | arXiv 稳定 ID、版本号、首次提交时间 | `GO`，作为首个非专利全文 provider；仅覆盖 arXiv 馆藏 |
| OpenAlex | 大规模论文全文/语义线索检索 | 题录、OA/PDF 地址，部分 PDF 仍会被源站拒绝 | OpenAlex ID、DOI、publication_date | `LEAD_ONLY`，实际 PDF/网页取得并校验后才能升级 |
| Crossref | DOI/标题/作者/日期检索 | 通常只有题录和 DOI 落地页 | DOI 与出版日期 | `LEAD_ONLY`，不能仅凭 Crossref 记录成为对比文件 |
| 人工导入 | 用户提供专利、论文、标准、手册、datasheet、网页快照等 | 原始文件 | 用户提供日期声明并配合外部佐证 | `GO`，保存原文件哈希、来源说明和日期证据；日期不明时保持待复核 |

## 3. 已完成的真实请求

### 3.1 专利路径

- 请求 `https://patents.google.com/patent/CN215310006U/zh` 返回 HTTP 200，取得 205,653 字节 HTML。
- 页面含 `CN215310006U` 的申请日 `2021-05-31`、公开日 `2021-12-28`、PDF URL、全文和引证数据。
- 请求 Google Patents XHR 查询端点返回结构化 JSON，包含公开编号、标题、摘要片段、优先权日、申请日、公开日、PDF 与附图路径。
- 从候选 `TWM311443U` 详情页取得真实 PDF；文件为 PDF 1.4、25 页、1,503,716 字节，SHA-256 为 `ab8e71202680015da7a81fa7f9f55af07766579d060222d7344fb9d58a858393`。

### 3.2 非专利路径

- arXiv Atom API 支持关键词、日期上限、分页和稳定版本 ID。
- 从 `arXiv:1404.6756v1` 取得真实 PDF；文件为 PDF 1.5、7 页、91,980 字节，SHA-256 为 `2226612750864866cdebae526f133c281dc2fdc7b02f1950df1a0803f3643c73`。
- OpenAlex 的精简字段查询返回 HTTP 200，并提供论文 ID、DOI、publication_date、OA 状态与 PDF 地址；其中一个源站 PDF 实测返回 403，证明“有 PDF URL”不等于“已取得文件”。
- Crossref 查询返回 HTTP 200 和 DOI/出版日期，但未提供可直接核验的全文，因此只能作为 lead。

### 3.3 2026-07-19 受控复测

- Google Patents 专利详情页复测返回 HTTP 200、`text/html`、892,068 字节；项目 adapter 从真实响应快照解析为 `retrieved_document`，得到公开编号、公开日、全文和内容哈希。当前 XHR 若省略公开前端固定发送的空 `exp` 参数会返回 HTTP 500；补齐后返回 HTTP 200、`application/json`、50,070 字节，adapter 从真实响应解析出 3 条 `lead`，该参数已加入契约回归。
- OpenAlex 真实查询返回 HTTP 200、`application/json`、94,587 字节；Crossref 真实查询返回 HTTP 200、`application/json`、13,266 字节。两个 adapter 对真实响应均只生成 `lead`，即使题录带 PDF URL 也不写入内容哈希。
- OpenAlex 返回的 Wiley PDF URL 实测为 HTTP 403、HTML；Crossref 返回的 MDPI 页面及 PDF URL也均为 HTTP 403、HTML。该结果再次证明题录中的 PDF URL 不能直接升级证据，组合 provider 必须保留该失败并以 `partial` 收口。
- arXiv Atom API 复测返回 HTTP 200、`application/atom+xml`、3,562 字节；随后取得 `arXiv:1309.2086v1` 的真实 PDF（HTTP 200、`application/pdf`、492,514 字节）。项目 adapter 对真实快照依次得到 `lead -> retrieved_document`；因本轮没有人工确认目标技术相关性，资格检查仍保持 `retrieved_document`，并记录 `technical_disclosure_not_verified`，没有为了跑通测试而伪造 `qualified_evidence`。
- 当前运行环境没有暴露 AgentKey 的 provider 工具，因此本轮仅记录 `UNAVAILABLE_IN_RUNTIME`，不宣称 AgentKey 已接入或验证。
- 当前受管网络把上述公开域名解析到 `198.18.0.0/15` 的代理 fake-IP。项目的 SSRF 校验按保留地址 fail-closed，真实请求在出站前被拒绝；上面的联网事实由独立、限时限字节的只读 `curl` 取得，再用项目 adapter 离线解析真实响应快照。正式 live provider 若要通过该网络，需增加显式可信代理/解析器配置，禁止为了兼容代理而全局放行保留地址。

## 4. 证据升级规则

```text
lead
  -> retrieved_document
     条件：实际取得 HTML/PDF/图片/原文件，并保存响应与 SHA-256
  -> qualified_evidence
     条件：内容可读、文献版本明确、公开日期可证明、来源链完整，且日期引擎按具体权利要求判定合格
```

- provider 自带的日期过滤只用于减少召回量，不产生法律资格结论。
- 同一专利族的不同公开文本、预印本与期刊版、网页不同快照均保存为独立文献版本。
- 下载失败、验证码、403、只有摘要、日期未知或内容不完整时保持 `lead`/`retrieved_document`，不得显示为合格证据。
- 普通现有技术必须在相应权利要求关键日前公开；中国抵触申请候选单独分类，只能用于新颖性路径；关键日后材料只能作为追溯线索。

## 5. MVP provider 顺序

1. `manual`：人工导入与固定 fixture，保证契约和证据链可重放。
2. `google_patents`：全球专利发现、取文、引证和家族入口。
3. `arxiv`：可直接取得全文的非专利来源。
4. `openalex`、`crossref`：扩展非专利召回，只生成 lead，后接取文器。
5. `epo_ops`：取得凭据后启用，用于题录、家族、引证和日期交叉核验。

## 6. Go / No-Go

结论为 `GO`：已有真实专利全文路径、真实非专利全文路径和人工导入路径，可以开始实现统一 provider 契约、证据状态机和日期引擎。

约束：Google Patents 与 arXiv 都不是全球穷尽性来源；MVP 输出必须明确检索范围与 provider 失败，不得宣称已经穷尽全部现有技术。正式结论仍需人工复核。

## 7. 参考入口

- EPO OPS：<https://www.epo.org/en/searching-for-patents/data/web-services/ops>
- WIPO PATENTSCOPE：<https://www.wipo.int/en/web/patentscope>
- WIPO IP API Catalog：<https://www.wipo.int/en/web/standards/ip-api-catalog/user-guide>
- USPTO Open Data Portal：<https://data.uspto.gov/>
- Google Patents：<https://patents.google.com/>
- OpenAlex API：<https://developers.openalex.org/api-reference/introduction>
- Crossref REST API：<https://api.crossref.org/>
- arXiv API：<https://info.arxiv.org/help/api/index.html>
