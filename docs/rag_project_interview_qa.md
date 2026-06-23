# Local Enterprise RAG 项目面试问答

本文档假设面试官围绕本项目做深入提问，给出可直接用于面试表达的参考答案。回答风格尽量贴近真实技术面试：先讲结论，再讲实现，再讲取舍和可优化点。

项目一句话：

> 这是一个本地企业文档 RAG 知识库系统，支持知识库管理、文档上传、解析、切块、关键词索引、向量检索、混合召回、基于证据的大模型问答和检索日志追踪。

---

## 1. 项目整体介绍

### Q1：请你简单介绍一下这个项目。

**参考回答：**

这个项目是一个本地企业文档 RAG 知识库系统。用户可以创建知识库，上传 PDF、DOCX、TXT、Markdown 等文档。系统会对文档进行解析、切块、建立 SQLite FTS5 关键词索引，并调用 embedding 模型生成向量。用户提问时，系统会先做检索，找到相关文档片段，再调用大模型基于这些证据生成带引用的答案。

技术上，后端使用 FastAPI，数据库使用 SQLite + SQLAlchemy，关键词检索使用 SQLite FTS5，向量检索支持 ChromaDB，也有本地 fallback。文档处理任务设计上接入了 Celery 和 Redis，本地默认用 eager 模式同步执行。大模型调用使用 OpenAI-compatible 接口，目前可以接 DashScope 的 `text-embedding-v4` 和 `qwen-plus`。

这个项目的重点不是单纯调用大模型，而是完整实现了 RAG 的工程链路：

```text
文档上传 -> 解析 -> 切块 -> 索引 -> 检索 -> 融合 -> 引用 -> 大模型回答 -> 日志追踪
```

---

### Q2：为什么要做这个项目？它解决了什么问题？

**参考回答：**

它解决的是企业私有文档问答的问题。通用大模型本身不知道企业内部制度、合同、报销流程、项目文档等私有知识。如果直接问模型，模型可能不知道，或者产生幻觉。

RAG 的做法是不把私有知识训练进模型参数，而是把文档先入库。用户提问时，系统先从文档库里检索相关片段，再把这些片段作为上下文交给大模型回答。

这样有几个好处：

- 文档更新快，不需要重新训练模型。
- 答案有引用，可以追溯来源。
- 适合企业私有知识场景。
- 可以通过检索日志分析回答质量问题。
- 成本比微调或训练低很多。

---

### Q3：这个项目和普通 ChatGPT 问答有什么区别？

**参考回答：**

普通 ChatGPT 问答主要依赖模型自身参数知识，而这个项目回答问题前会先检索用户上传的文档。

区别可以概括为：

| 维度 | 普通问答 | 本项目 RAG 问答 |
|---|---|---|
| 知识来源 | 模型训练参数 | 用户上传文档 |
| 私有知识 | 默认不知道 | 可通过文档入库获得 |
| 可追溯性 | 较弱 | 有 citations |
| 更新成本 | 需要重新提供上下文或训练 | 上传新文档即可 |
| 幻觉控制 | 较弱 | 通过证据和拒答降低幻觉 |

本项目里，大模型不是直接回答，而是基于检索出的 chunk 回答。

---

## 2. 系统架构

### Q4：项目整体架构是怎样的？

**参考回答：**

项目主要分为几层：

1. API 层  
   使用 FastAPI，定义知识库、文档、搜索、聊天、日志等接口。

2. 服务层  
   包含上传存储、文档处理、解析、切块、embedding、向量库、检索、聊天生成等业务逻辑。

3. 数据层  
   使用 SQLite + SQLAlchemy 保存知识库、文档、chunk、会话、消息、检索日志和后台任务。

4. 检索层  
   使用 SQLite FTS5 做 sparse 检索，使用 embedding + 向量库做 dense 检索，再用 RRF 融合。

5. 模型层  
   embedding 调用 DashScope `text-embedding-v4`，聊天生成调用 OpenAI-compatible 的 `qwen-plus`。

6. 任务层  
   使用 Celery 设计后台任务，本地可以 eager 同步执行，Docker 下可以接 Redis 和 worker。

整体链路：

```text
用户/Swagger/Streamlit
  -> FastAPI
  -> Service 层
  -> SQLite / FTS5 / Vector Store
  -> Embedding API / Chat API
```

---

### Q5：为什么选择 FastAPI？

**参考回答：**

我选择 FastAPI 有几个原因：

1. 它对类型标注和 Pydantic 支持很好，接口 schema 清晰。
2. 自动生成 Swagger/OpenAPI 文档，调试上传、搜索、聊天接口很方便。
3. 性能和开发效率都不错。
4. 适合构建轻量 API 服务。
5. 和异步上传文件、依赖注入、统一异常处理结合比较自然。

在这个项目中，FastAPI 主要负责：

- 路由定义。
- 参数校验。
- 文件上传。
- 数据库 Session 注入。
- 错误响应封装。
- Swagger 页面调试。

---

### Q6：为什么使用 SQLite？它适合生产吗？

**参考回答：**

这个项目定位是本地单用户知识库 MVP，所以 SQLite 很合适。它不需要单独部署数据库服务，文件即数据库，启动成本低，也方便测试。

SQLite 在本项目里保存：

- 知识库信息。
- 文档元数据。
- chunk 文本。
- 会话和消息。
- 检索日志。
- 后台任务状态。

同时项目开启了 SQLite 的几个 pragma：

```text
WAL
foreign_keys
busy_timeout
```

WAL 可以提升读写并发体验，foreign key 保证级联删除，busy_timeout 避免短时间写锁冲突立刻失败。

不过如果要做多用户、高并发、权限系统和大规模数据，SQLite 就不是最佳选择了，后续应该迁移到 PostgreSQL。

---

### Q7：这个项目为什么既有 SQLAlchemy create_all，又有 Alembic？

**参考回答：**

`create_all` 是为了本地 MVP 开箱即用。用户启动 FastAPI 后，系统会自动创建缺失表和 FTS5 虚拟表，降低试用门槛。

Alembic 是正式的数据库迁移工具，适合 schema 变更版本管理，比如新增字段、修改索引、升级生产数据库。

简单说：

```text
create_all -> 本地快速启动
Alembic    -> 正式迁移管理
```

生产环境更推荐通过 Alembic 管理数据库结构。

---

## 3. 文档上传与处理

### Q8：用户上传文档后，系统具体做了什么？

**参考回答：**

上传文档后，系统会走一条完整的入库流程：

1. 检查知识库是否存在。
2. 读取上传文件。
3. 校验文件大小。
4. 计算 SHA256。
5. 清理文件名，防止路径穿越。
6. 校验扩展名和 MIME。
7. 检查同一知识库内是否已上传相同内容。
8. 把文件保存到 `data/uploads/{kb_id}/`。
9. 创建 `Document` 记录，状态为 `pending`。
10. 创建 `BackgroundTask` 记录。
11. 触发文档处理任务。
12. 解析文档、切块、保存 chunk、建 FTS5 索引、调用 embedding、建向量索引。
13. 最后把文档状态更新为 `completed`。

如果中间失败，会记录：

```text
status = failed
failed_stage = 失败阶段
error_message = 错误信息
```

---

### Q9：上传时为什么要计算 SHA256？

**参考回答：**

SHA256 用于文件内容去重。它不是根据文件名去重，而是根据文件内容去重。

这样做的原因是：

- 同一个文件改名后再次上传，仍能识别为重复。
- 同一知识库内避免重复索引相同文档。
- 节省存储和 embedding 成本。

项目中设置了唯一约束：

```text
knowledge_base_id + sha256
```

也就是说：

- 同一个知识库内，相同内容不能重复上传。
- 不同知识库可以上传相同内容。

---

### Q10：为什么要校验扩展名和 MIME？

**参考回答：**

这是上传安全和解析稳定性的要求。

扩展名校验可以提前拒绝明显不支持的文件，比如 `.exe`。MIME 校验可以进一步判断上传内容类型是否合理。

项目允许：

```text
.pdf
.docx
.txt
.md
.markdown
```

同时对 text、PDF、DOCX 等 MIME 做白名单。

当然，MIME 是客户端提供的，不能完全信任，所以项目是“扩展名 + MIME”结合判断。生产环境还可以进一步做文件魔数检测和杀毒扫描。

---

### Q11：文档处理为什么设计成 BackgroundTask / Celery？

**参考回答：**

文档处理是耗时任务，尤其是 PDF/DOCX 解析、OCR、embedding API 调用和向量索引。如果在 HTTP 请求里同步做，上传大文档时接口可能阻塞很久，甚至超时。

所以项目设计了后台任务入口：

```text
上传接口 -> 创建任务记录 -> 触发文档处理
```

本地开发默认：

```text
RAG_CELERY_TASK_ALWAYS_EAGER=true
```

任务会同步执行，方便测试。

Docker 或生产环境可以改成：

```text
RAG_CELERY_TASK_ALWAYS_EAGER=false
```

然后由 Redis + Celery worker 异步处理。

---

### Q12：文档处理有哪些阶段？

**参考回答：**

主要阶段在 `app/services/processing.py` 里：

```text
validating
parsing
chunking
saving_chunks
indexing_fts
embedding
indexing_vectors
verifying
completed
```

含义：

- `validating`：确认 Document 和原始文件存在。
- `parsing`：根据格式解析文档。
- `chunking`：切成适合检索的小片段。
- `saving_chunks`：保存到 `document_chunks` 表。
- `indexing_fts`：写入 SQLite FTS5 虚拟表。
- `embedding`：调用 embedding API。
- `indexing_vectors`：写入向量库或 local fallback。
- `verifying`：更新 chunk_count 和状态。
- `completed`：处理完成。

每个阶段都会更新 `BackgroundTask`，前端可以查询进度。

---

## 4. 文档解析

### Q13：项目支持哪些文档格式？

**参考回答：**

支持：

```text
TXT
Markdown
DOCX
PDF
```

解析逻辑在：

```text
app/services/parsers.py
```

不同格式：

- TXT：直接解码文本。
- Markdown：解析标题，把标题作为 section_title。
- DOCX：优先 Docling，回退到 python-docx。
- PDF：优先 Docling，回退到 pypdf。

输出都会统一成：

```text
ParsedDocument
  -> list[DocumentElement]
```

这样后续切块和索引不需要关心原始格式。

---

### Q14：为什么要把不同文档解析成统一结构？

**参考回答：**

因为 RAG 后续流程只需要处理文本和来源信息，不应该关心文件格式。

如果 PDF、DOCX、Markdown 后续分别处理，代码会很复杂。统一成 `ParsedDocument` 和 `DocumentElement` 后，后续流程可以统一：

```text
ParsedDocument
  -> chunk_document()
  -> DocumentChunk
  -> FTS5 / Embedding / Vector Store
```

`DocumentElement` 保存：

```text
content
page_number
section_title
element_type
metadata
```

这样后续引用答案时，可以知道片段来自哪一页、哪个章节、什么元素类型。

---

### Q15：PDF 解析有什么限制？

**参考回答：**

当前项目对 PDF 的支持主要是文本型 PDF，也就是可以复制文字的 PDF。

如果是扫描版 PDF，`pypdf` 很可能抽不到文字。项目里预留了 PaddleOCR fallback，但当前 MVP 没完整实现“PDF 页面渲染成图片 -> OCR 识别”的流程。

所以目前限制是：

- 文本型 PDF 支持较好。
- 扫描版 PDF 需要补 OCR 流程。
- 表格、图片、复杂版式还需要 Docling 或更强解析器处理。

---

## 5. Chunking 切块

### Q16：你的 chunking 是怎么实现的？

**参考回答：**

chunking 逻辑在：

```text
app/services/chunking.py
```

核心函数是：

```python
chunk_document(parsed, chunk_size, chunk_overlap)
```

整体流程：

1. 遍历解析出来的每个 `DocumentElement`。
2. 对文本做规范化，比如统一换行、合并空格、压缩多余空行。
3. 调用 `split_text()` 按 `chunk_size` 切分。
4. 尽量在换行、中文句号、英文句号等自然边界切。
5. 使用 `chunk_overlap` 让相邻 chunk 保留重叠上下文。
6. 对每个 chunk 计算 SHA256 hash。
7. 用 hash 去重。
8. 估算 token 数。
9. 保留页码、章节标题、元素类型和 metadata。

输出是 `ChunkCandidate`，后续会保存成数据库里的 `DocumentChunk`。

---

### Q17：chunk_size 和 chunk_overlap 分别有什么作用？

**参考回答：**

`chunk_size` 表示每个 chunk 最大长度。项目里近似按字符数切，不是严格 token。

`chunk_overlap` 表示相邻 chunk 的重叠长度。

例如：

```text
chunk_size = 100
chunk_overlap = 20
```

大概效果：

```text
chunk 1: 1 - 100
chunk 2: 81 - 180
chunk 3: 161 - 260
```

重叠的作用是防止关键信息刚好被切在边界两侧。

必须满足：

```text
chunk_overlap < chunk_size
```

项目默认：

```text
chunk_size = 800
chunk_overlap = 120
```

---

### Q18：为什么 chunking 不直接按固定长度硬切？

**参考回答：**

硬切会把一句话或一个语义单元切断，导致检索和问答效果下降。

项目里会尽量找自然边界：

```text
换行
中文句号
英文句号
```

如果自然边界出现在 chunk 后半段，就在该位置切。这样可以降低切断句子的概率。

不过当前实现还是轻量版，后续可以升级为：

- token-based splitter。
- recursive character splitter。
- 基于标题层级的结构化切分。
- 表格单独切分。

---

### Q19：chunking 为什么要做 hash 去重？

**参考回答：**

有些文档会重复出现页眉、页脚、标题、免责声明或重复段落。如果不去重，这些重复内容会污染检索结果。

项目对每个 chunk 计算：

```text
sha256(content)
```

同一文档中如果 hash 已经出现过，就跳过。

好处：

- 减少重复 chunk。
- 降低 embedding 成本。
- 改善检索质量。

---

## 6. Embedding 和向量检索

### Q20：embedding 在项目中起什么作用？

**参考回答：**

embedding 的作用是把文本转成向量，使系统能够做语义检索。

例如：

```text
"报销需要多久提交"
"票据应在多少天内提交"
```

这两句话字面不完全相同，但语义相近。关键词检索可能不稳定，embedding 可以把它们映射到相近向量空间，从而召回相关 chunk。

在项目中，embedding 用于两处：

1. 文档入库时：每个 chunk 转成向量。
2. 用户查询时：query 转成向量。

然后通过向量相似度找相关 chunk。

---

### Q21：embedding_dimension 是什么？

**参考回答：**

`embedding_dimension` 是向量维度，也就是 embedding 输出数组的长度。

比如：

```text
text-embedding-v4 -> 1024 维
```

一段文本会变成：

```text
[0.01, -0.23, 0.45, ...]  共 1024 个数字
```

这个维度必须和模型实际输出一致。不能随便填，否则向量计算和向量库存储可能出问题。

当前外部 API 版本使用：

```text
DashScope text-embedding-v4
embedding_dimension = 1024
```

---

### Q22：项目里的向量库是怎么做的？

**参考回答：**

项目抽象了一个 `BaseVectorStore`，目前有两个实现：

```text
ChromaVectorStore
LocalVectorStore
```

ChromaDB 是真正的本地向量数据库，可以持久化存储 embedding，查询效率更适合数据量较大的场景。

LocalVectorStore 是 fallback。它不真正保存向量，而是在查询时：

1. 把 query 转成 embedding。
2. 遍历当前知识库所有 chunk。
3. 重新计算每个 chunk 的 embedding。
4. 计算 cosine similarity。
5. 按相似度排序。

Local fallback 的优点是零额外依赖，MVP 能跑通；缺点是数据量大时慢，并且每次查询都会重复调用 embedding。

---

### Q23：cosine similarity 是什么？

**参考回答：**

Cosine similarity 是余弦相似度，用来衡量两个向量方向是否接近。

如果两个文本语义相近，它们的 embedding 向量方向通常也接近，cosine similarity 就较高。

项目里 dense retrieval 的相似度就是基于它。

简单理解：

```text
分数越高 -> 文本语义越相似
```

---

## 7. Sparse / Dense / Hybrid Retrieval

### Q24：retrieval 检索流程是怎么做的？

**参考回答：**

检索逻辑在：

```text
app/services/retrieval.py
```

入口是：

```python
retrieve(db, knowledge_base_id, query, top_k, retrieval_mode)
```

支持四种模式：

```text
sparse
dense
hybrid
hybrid_rerank
```

流程：

```text
如果是 sparse/hybrid/hybrid_rerank -> 做 FTS5 关键词检索
如果是 dense/hybrid/hybrid_rerank -> 做向量检索
如果是 hybrid/hybrid_rerank -> 用 RRF 融合两路结果
如果是 hybrid_rerank -> 对融合结果再做重排
最后取 top_k
写 retrieval log
```

---

### Q25：sparse 检索怎么实现？

**参考回答：**

sparse 检索使用 SQLite FTS5。

流程：

1. 把用户 query 通过 `sanitize_fts_query()` 处理成 FTS5 查询。
2. 查询 `document_chunks_fts` 虚拟表。
3. 使用 `bm25()` 排序。
4. 根据 chunk_id 回表加载 `DocumentChunk`。

SQL 类似：

```sql
SELECT chunk_id, bm25(document_chunks_fts) AS bm25_score
FROM document_chunks_fts
WHERE document_chunks_fts MATCH :query
  AND knowledge_base_id = :knowledge_base_id
ORDER BY bm25_score
LIMIT :limit
```

FTS5 的 BM25 分数越小越相关，项目里会转成越大越好的 score。

如果 FTS5 查询失败或没有结果，会 fallback 到 `LIKE` 搜索。

---

### Q26：dense 检索怎么实现？

**参考回答：**

dense 检索使用 embedding 向量相似度。

流程：

```text
query -> embedding -> vector_store.query() -> chunk_id -> 回表加载 chunk
```

如果使用 ChromaDB，直接查 collection。

如果使用 LocalVectorStore，则遍历数据库中该知识库所有 chunk，逐个计算相似度。

最终返回：

```text
RankedChunk(chunk, score, source="dense", rank)
```

---

### Q27：为什么要 hybrid retrieval？

**参考回答：**

因为 sparse 和 dense 各有优缺点。

Sparse 优点：

- 精确。
- 快。
- 适合数字、条款、专有名词。

Sparse 缺点：

- 不理解语义。
- 同义改写可能搜不到。

Dense 优点：

- 能理解语义相近表达。
- 对自然语言问题更友好。

Dense 缺点：

- 可能召回语义相关但不精确的内容。
- 成本更高。
- 依赖 embedding 模型。

Hybrid 同时使用两者，再融合，可以提升召回稳定性。

---

## 8. RRF 和 rerank

### Q28：RRF 是什么？项目里为什么用它？

**参考回答：**

RRF 是 Reciprocal Rank Fusion，用来融合多路检索结果。

在项目里，sparse 检索和 dense 检索的原始分数不可比：

```text
FTS5/BM25 分数
向量 cosine similarity 分数
```

它们不是同一尺度，不能直接相加。

RRF 不看原始分数，只看排名：

```python
score += 1.0 / (rrf_k + rank)
```

如果一个 chunk 在 sparse 和 dense 都排名靠前，它的融合分数就会更高。

所以 RRF 解决的是：

```text
多路检索结果如何合并
```

---

### Q29：RRF 和 rerank 作用一样吗？

**参考回答：**

不一样。

RRF 是融合：

```text
把 sparse 和 dense 两路结果合并成一组候选
```

rerank 是重排：

```text
对融合后的候选结果再重新排序
```

一句话：

```text
RRF 是把多路队伍合并成一队
rerank 是对这一队重新排顺序
```

项目里的 rerank 是轻量词面重叠：

```text
query 中的词如果出现在 chunk 中，就加分
```

后续可以替换成 cross-encoder 或 BGE reranker。

---

### Q30：RRF 和 hybrid_rerank 是二选一吗？

**参考回答：**

不是。

在项目里：

```text
hybrid_rerank = sparse + dense + RRF + rerank
```

`hybrid` 是：

```text
sparse + dense + RRF
```

`hybrid_rerank` 是在 `hybrid` 基础上再加一步 rerank。

表格：

| 模式 | sparse | dense | RRF | rerank |
|---|---|---|---|---|
| sparse | 有 | 无 | 无 | 无 |
| dense | 无 | 有 | 无 | 无 |
| hybrid | 有 | 有 | 有 | 无 |
| hybrid_rerank | 有 | 有 | 有 | 有 |

---

### Q31：你项目里的 rerank 有什么问题？

**参考回答：**

当前 rerank 是 MVP 级别的轻量实现，只计算词面重叠。

优点：

- 简单。
- 快。
- 不需要额外模型。

缺点：

- 中文没有分词时，`query.split()` 效果有限。
- 不理解深层语义。
- 无法判断句子级相关性。

后续可以优化：

- 接入中文分词。
- 使用 BGE reranker。
- 使用 cross-encoder。
- 使用 LLM rerank。

---

## 9. Chat 和 Citation

### Q32：chat 接口和 search 接口有什么区别？

**参考回答：**

`search` 只做检索，返回相关 chunk。

`chat` 会在检索后继续：

1. 生成 citations。
2. 校验 citations。
3. 把证据交给大模型。
4. 生成自然语言答案。
5. 保存 user message 和 assistant message。
6. 更新 retrieval log。

所以：

```text
search = 检索调试接口
chat   = 面向用户的问答接口
```

---

### Q33：citation 是怎么生成和校验的？

**参考回答：**

代码在：

```text
app/services/citations.py
```

检索得到 final evidence 后，系统为每个 chunk 生成 citation：

```text
citation_id
chunk_id
document_id
source_filename
page_number
section_title
quote
```

然后做校验：

```python
quote in chunk.content
```

只有 quote 确实存在于原 chunk 中，citation 才有效。

这样可以避免模型生成不存在的引用。

---

### Q34：如果没有检索到证据，系统怎么处理？

**参考回答：**

如果没有有效 citation，系统会拒答：

```text
refusal = true
```

并返回类似：

```text
I do not have enough evidence...
```

这比强行让模型回答更安全，因为企业知识库场景中，错误答案和幻觉风险较高。

---

### Q35：生成答案时如何约束大模型？

**参考回答：**

项目在调用 OpenAI-compatible chat API 时，会把证据组织成 prompt，并在 system message 里要求：

```text
只能基于提供的 evidence 回答
证据不足时拒答
使用 evidence 中已有的 citation id
```

这样可以降低幻觉，并让答案带引用。

当前外部 API 使用：

```text
qwen-plus
```

---

## 10. RetrievalLog 可观测性

### Q36：为什么要设计 retrieval_logs？

**参考回答：**

RAG 系统的效果问题往往不只出在大模型，更多时候出在检索阶段。

比如：

- sparse 没命中。
- dense 召回不准。
- RRF 融合排序有问题。
- rerank 把正确结果排后面。
- final evidence 不包含答案。
- evidence 正确但模型回答错。

所以项目把每次检索的全过程写入 `RetrievalLog`：

```text
sparse_results
dense_results
fusion_results
rerank_results
final_evidence
latency
fallback_used
model_name
```

这样可以定位问题发生在哪一步。

---

### Q37：如果用户说“答案不对”，你会怎么排查？

**参考回答：**

我会按 retrieval log 分层排查：

1. 看 `final_evidence` 是否包含正确证据。  
   如果没有，问题在检索。

2. 看 `sparse_results` 是否有正确 chunk。  
   如果没有，可能关键词、分词、FTS 查询有问题。

3. 看 `dense_results` 是否有正确 chunk。  
   如果没有，可能 embedding 效果、chunking 或向量库有问题。

4. 看 `fusion_results` 和 `rerank_results`。  
   如果正确 chunk 曾经出现但最终没进 top_k，说明融合或重排需要优化。

5. 如果 `final_evidence` 正确但回答错，说明问题在大模型生成或 prompt。

这种可观测性是 RAG 工程里很重要的一点。

---

## 11. 配置和外部 API

### Q38：项目是如何管理配置的？

**参考回答：**

配置集中在：

```text
app/core/config.py
```

使用 `pydantic-settings`，支持 `.env` 和环境变量。

所有配置项使用：

```text
RAG_
```

前缀。

比如：

```text
RAG_DATABASE_URL
RAG_VECTOR_STORE_BACKEND
RAG_EMBEDDING_PROVIDER
RAG_EMBEDDING_MODEL
RAG_GENERATION_PROVIDER
RAG_CHAT_MODEL
```

项目还支持 `RAG_EXTERNAL_ENV_FILE`，可以从外部 env 文件读取 API key，避免把密钥复制到项目目录。

---

### Q39：项目调用外部大模型 API 了吗？

**参考回答：**

项目有两种运行模式。

mock 模式：

```text
不调用外部 API
embedding 使用 hash_embedding
generation 直接拼接证据
```

外部 API 模式：

```text
embedding 调用 DashScope text-embedding-v4
chat 调用 qwen-plus
```

当前 8000 实例是外部 API 模式。

---

### Q40：接 DashScope 时遇到过什么问题？

**参考回答：**

遇到过 DashScope embedding 接口的 batch size 限制。

一开始上传文档时，系统把所有 chunk 一次性传给 embeddings 接口。如果 chunk 数超过限制，DashScope 返回 400。

我后来在 `EmbeddingService._embed_openai()` 里增加了自动分批：

```text
DashScope 每批最多 10 条 input
```

这样长文档也能正常处理。

这个问题体现了外部 API 接入时需要关注：

- batch size 限制。
- rate limit。
- timeout。
- 错误处理。
- 重试策略。

---

## 12. 安全性和健壮性

### Q41：项目里有哪些安全设计？

**参考回答：**

当前项目主要有这些基础安全设计：

1. 文件扩展名白名单。
2. MIME 类型校验。
3. 上传大小限制。
4. 文件名清理，防止路径穿越。
5. SHA256 去重。
6. 统一异常响应，不直接暴露内部 traceback。
7. request_id 追踪。
8. API key 通过环境变量读取，不写入代码。

不过它还是 MVP，没有做：

- 用户登录。
- 权限控制。
- 多租户隔离。
- 文件杀毒。
- API 限流。
- 审计日志。

这些是生产化方向。

---

### Q42：如果文档处理失败，系统怎么恢复？

**参考回答：**

失败时会记录：

```text
Document.status = failed
failed_stage = 当前阶段
error_message = 错误信息
```

同时 `BackgroundTask` 也会记录失败阶段和错误。

用户可以调用：

```text
POST /documents/{document_id}/retry
```

如果需要重新解析和重建索引，可以调用：

```text
POST /documents/{document_id}/reindex
```

`reindex` 会清理旧的 chunk、FTS5 和向量索引，然后重新处理原始文件。

---

## 13. 测试和质量保障

### Q43：项目有哪些测试？

**参考回答：**

测试在 `tests/` 目录。

主要有三类：

1. API 流程测试  
   覆盖 health、ready、创建知识库、上传文档、搜索、聊天、重复文档校验。

2. chunking 和评估测试  
   测试切块稳定性、去重，以及 recall、precision、MRR、NDCG 等指标函数。

3. storage 测试  
   测试文件名清理、扩展名校验、MIME 校验。

测试里使用临时 SQLite 数据库和 mock embedding/generation，避免依赖外部 API。

---

### Q44：为什么测试用 mock，不用真实大模型 API？

**参考回答：**

自动化测试应该稳定、快速、低成本。

如果测试依赖真实大模型 API，会有几个问题：

- 需要 API key。
- 网络不稳定。
- 有调用成本。
- 模型输出不完全确定。
- CI 环境不一定可访问外部 API。

所以单元和集成测试使用 mock embedding 和 mock generation，只验证系统链路和业务逻辑。外部 API 可以通过单独的手动集成测试验证。

---

## 14. 部署和运行

### Q45：项目怎么运行？

**参考回答：**

本地开发：

```powershell
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

外部 API 版需要配置：

```text
RAG_EMBEDDING_PROVIDER=dashscope
RAG_EMBEDDING_MODEL=text-embedding-v4
RAG_EMBEDDING_DIMENSION=1024
RAG_GENERATION_PROVIDER=openai-compatible
RAG_CHAT_MODEL=qwen-plus
RAG_OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

Docker Compose 可以启动：

```text
api
worker
redis
streamlit
```

---

### Q46：为什么现在使用 Swagger 而不是前端？

**参考回答：**

项目有 Streamlit 前端，但当前本机环境的 Streamlit 依赖存在 `pandas/numpy` 二进制兼容问题，所以没有启动前端。

FastAPI 自动生成的 Swagger 已经可以完成：

- 创建知识库。
- 上传文档。
- 搜索。
- 问答。
- 查看日志。

所以当前主要通过：

```text
http://127.0.0.1:8000/docs
```

进行测试。

生产或展示场景可以修复 Streamlit 依赖，或者重新做一个 React/Vue 前端。

---

## 15. 项目不足和优化

### Q47：这个项目目前有什么不足？

**参考回答：**

主要不足：

1. 还是单用户 MVP，没有登录、权限和多租户。
2. SQLite 不适合高并发多用户场景。
3. 当前 local vector store 数据量大时性能差。
4. OCR 没完整实现，扫描 PDF 支持弱。
5. rerank 只是简单词面重叠，不够强。
6. 缺少外部 API 的重试、限流和成本统计。
7. Streamlit 前端比较简单。
8. 没有文件预览和批量上传。

---

### Q48：如果让你继续优化，你会优先做什么？

**参考回答：**

我会按优先级做：

1. 启用 ChromaDB 或更专业的向量数据库，避免 local fallback 每次线性扫描。
2. 引入专业 reranker，比如 BGE reranker 或 cross-encoder。
3. 完整实现 OCR，支持扫描版 PDF。
4. 加入用户、权限和知识库隔离。
5. 做一个正式 Web 前端。
6. 增加 API 调用重试、限流、超时和成本统计。
7. 建立标准评估集，持续评估 recall@k、MRR、citation validity。

---

### Q49：如果文档量从几十份变成几十万份，系统要怎么改？

**参考回答：**

需要从多个层面改：

数据库：

```text
SQLite -> PostgreSQL
```

向量检索：

```text
LocalVectorStore -> ChromaDB / Milvus / Qdrant / Elasticsearch dense vector
```

任务处理：

```text
同步 eager -> Celery worker 集群
```

索引：

```text
批量 embedding
增量索引
失败重试
速率限制
```

检索：

```text
召回阶段分页或 ANN
reranker 只处理 top N
缓存热门 query
```

存储：

```text
原文件放对象存储
数据库只存 metadata
```

可观测：

```text
日志、指标、trace、成本统计
```

---

## 16. 代码设计追问

### Q50：为什么服务层拆这么多文件？

**参考回答：**

因为 RAG 链路比较长，如果全部写在一个 service 里会很难维护。

现在拆分为：

```text
storage       上传文件校验和落盘
documents     文档业务入口
processing    文档处理主流程
parsers       文档解析
chunking      切块
embeddings    embedding API
vector_store  向量库抽象
retrieval     检索、融合、重排
chat          问答生成
citations     引用
logs          检索日志
```

这样的好处是：

- 每个模块职责清晰。
- 方便单独测试。
- 后续替换某个环节更容易。
- 面对 RAG 链路问题时容易定位。

---

### Q51：为什么需要 request_id？

**参考回答：**

request_id 用来追踪一次请求。

项目中每个请求都会有：

```text
X-Request-ID
```

如果客户端没传，系统生成 UUID。

这个 request_id 会进入：

- JSON 日志。
- retrieval log。
- 错误响应。

排查问题时，可以通过 request_id 把 API 请求、后端日志和检索日志串起来。

---

### Q52：为什么要统一 AppError？

**参考回答：**

统一业务异常可以让 API 错误响应稳定。

例如：

```json
{
  "code": "document_not_found",
  "message": "Document not found.",
  "details": {},
  "request_id": "..."
}
```

前端可以根据 `code` 做逻辑判断，而不是解析自然语言。

同时避免不同地方抛出的异常格式不一致。

---

## 17. 面试官可能深挖的问题

### Q53：你怎么判断 RAG 系统效果好不好？

**参考回答：**

我会分两层评估。

第一层是检索评估：

- Recall@K：相关 chunk 有没有被召回。
- Precision@K：召回结果里有多少是相关的。
- HitRate@K：前 K 个结果是否至少命中一个相关证据。
- MRR@K：第一个相关结果排得多靠前。
- NDCG@K：综合考虑相关性和排序位置。

第二层是生成评估：

- 答案是否正确。
- 引用是否有效。
- 是否基于证据。
- 证据不足时是否拒答。
- 是否有幻觉。

项目里已经实现了一些检索指标函数，后续可以加标准评估集自动跑。

---

### Q54：如果模型回答时没有引用怎么办？

**参考回答：**

当前系统会在生成前构造 citations，并要求模型使用 citation id。生成后返回的 citations 由系统保存，而不是完全依赖模型自己生成。

如果模型答案没有正确引用，需要优化：

- prompt 更严格。
- 答案格式约束。
- 使用结构化输出。
- 后处理检查答案中是否包含 `[1]` 这种引用。
- 如果缺引用，可以拒答或重新生成。

---

### Q55：如果 dense 检索召回很差怎么办？

**参考回答：**

我会从几个方向排查：

1. embedding 模型是否适合中文和业务领域。
2. chunk 是否过短或过长。
3. query 是否需要 rewrite。
4. 文档解析是否丢了关键信息。
5. 向量库是否保存正确。
6. top_k 是否太小。

优化方式：

- 换更强 embedding 模型。
- 调整 chunk_size 和 overlap。
- 使用 hybrid 而不是纯 dense。
- 加 reranker。
- 做 query expansion。

---

### Q56：为什么不用微调模型？

**参考回答：**

因为这个场景更适合 RAG。

企业文档更新频繁，如果用微调：

- 成本高。
- 更新慢。
- 可追溯性差。
- 很难保证模型记住最新文档。

RAG 只需要更新文档索引，问题回答时实时检索最新证据，更适合企业知识库。

微调更适合：

- 固定任务格式。
- 风格对齐。
- 特定输出规范。

而不是频繁变化的知识注入。

---

### Q57：这个项目怎么防止大模型幻觉？

**参考回答：**

项目主要从三方面降低幻觉：

1. 先检索证据，不让模型凭空回答。
2. system prompt 要求模型只能基于 evidence 回答。
3. citation validation 确保引用片段真实存在于原文。
4. 没有证据时 refusal，不强行回答。
5. 保存 retrieval log，便于追溯答案来源。

但这不能百分百消除幻觉，后续还可以加入：

- 更严格结构化输出。
- 答案-证据一致性检查。
- LLM judge。
- 引用覆盖率检查。

---

## 18. 最后一段综合回答模板

### Q58：如果面试官让你完整介绍项目，你可以这样回答

**参考回答：**

我做的是一个本地企业文档 RAG 知识库系统，目标是让用户上传企业内部文档后，可以基于这些文档进行搜索和问答。

项目后端使用 FastAPI，数据库使用 SQLite + SQLAlchemy，文档上传后会保存原文件和 metadata。处理流程包括文档解析、切块、保存 chunk、建立 SQLite FTS5 关键词索引、调用 DashScope embedding API 生成向量，并写入向量检索层。文档处理任务设计上接入了 Celery，本地用 eager 模式同步执行，生产可以切换到 Redis + worker。

问答时，系统会先做检索。检索分为 sparse 和 dense 两路：sparse 使用 FTS5 + BM25，适合精确关键词；dense 使用 embedding 向量相似度，适合语义召回。两路结果通过 RRF 按排名融合，如果使用 hybrid_rerank，还会在融合结果上做一次轻量词面重排。最终 top_k chunk 会作为 evidence，生成 citations，并交给 qwen-plus 生成带引用的答案。

我比较关注 RAG 的可观测性，所以每次检索都会写 retrieval log，记录 sparse、dense、fusion、rerank 和 final evidence。这样如果答案不好，可以判断问题出在召回、融合、重排还是生成阶段。另外，文档处理失败会记录 failed_stage 和 error_message，并支持 retry 和 reindex。

这个项目目前是单机 MVP，后续可以优化为 PostgreSQL、ChromaDB 或专业向量数据库，接入更强 reranker，补完整 OCR，增加用户权限和正式前端，并建立标准评估集持续评估检索和问答质量。

---

## 19. 可以反问面试官的问题

如果面试官问完项目，你也可以反问：

1. 你们实际业务中更关注 RAG 的召回率，还是答案准确率？
2. 你们现在的知识库文档主要是 PDF、Word，还是网页/数据库？
3. 你们更倾向私有化部署模型，还是调用云端 API？
4. 对引用可追溯性有没有强要求？
5. 你们有没有现成的 QA 评估集？

这些反问能体现你理解 RAG 项目落地重点。
