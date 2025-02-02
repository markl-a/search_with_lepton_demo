import concurrent.futures # 用於創建異步執行的模塊
import glob # 用於檔案路徑名模式匹配
import json # 處理JSON數據
import os # 與操作系統交互，例如讀取環境變量
import re # 正則表達式，用於文本匹配和替換
import threading # 提供對線程的支持
import requests # 發送HTTP請求
import traceback # 追踪異常
from typing import Annotated, List, Generator, Optional # 強化代碼類型提示

from fastapi import HTTPException # FastAPI異常處理
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse # FastAPI響應類型
import httpx # 异步HTTP客户端
from loguru import logger # 日誌處理

import leptonai # 導入LeptonAI庫
from leptonai import Client # LeptonAI客戶端
from leptonai.kv import KV # LeptonAI的鍵值存儲
from leptonai.photon import Photon, StaticFiles # LeptonAI的Photon服務和靜態文件處理
from leptonai.photon.types import to_bool # 輔助函數，轉換字符串到布爾值
from leptonai.api.workspace import WorkspaceInfoLocalRecord  # 處理工作空間信息
from leptonai.util import tool # 通用工具

################################################################################
# Constant values for the RAG model. 接下來的部分定義了一系列常量和默認值，用於設定搜索引擎API的端點和其他配置。
################################################################################

# 定義搜索函數，分別使用 Bing、Google、Serper 和 SearchApi.io 進行搜索並返回上下文。
# Search engine related. You don't really need to change this.
BING_SEARCH_V7_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"
BING_MKT = "en-US"
GOOGLE_SEARCH_ENDPOINT = "https://customsearch.googleapis.com/customsearch/v1"
SERPER_SEARCH_ENDPOINT = "https://google.serper.dev/search"
SEARCHAPI_SEARCH_ENDPOINT = "https://www.searchapi.io/api/v1/search"

# 標定要從搜索引擎參考的內容數量
# Specify the number of references from the search engine you want to use.
# 8 is usually a good number.
REFERENCE_COUNT = 8

# 指定搜索引擎的默認超時時間。如果搜索引擎在此時間內沒有回應，這邊將返回一個代表錯誤的標記。
# Specify the default timeout for the search engine. If the search engine
# does not respond within this time, we will return an error.
DEFAULT_SEARCH_ENGINE_TIMEOUT = 5

# 設定的預設字串
# If the user did not provide a query, we will use this default query.
_default_query = "Who said 'live long and prosper'?"

# 這真的是RAG模型最重要的部分。它指導模型如何生成答案。當然，不同的模型可能會有不同的行為，我們還沒有調整提示以使其最優化 - 這留給您，應用程序創建者，作為一個開放問題。
# This is really the most important part of the rag model. It gives instructions
# to the model on how to generate the answer. Of course, different models may
# behave differently, and we haven't tuned the prompt to make it optimal - this
# is left to you, application creators, as an open problem.
_rag_query_text = """
You are a large language AI assistant built by Lepton AI. You are given a user question, and please write clean, concise and accurate answer to the question. You will be given a set of related contexts to the question, each starting with a reference number like [[citation:x]], where x is a number. Please use the context and cite the context at the end of each sentence if applicable.

Your answer must be correct, accurate and written by an expert using an unbiased and professional tone. Please limit to 1024 tokens. Do not give any information that is not related to the question, and do not repeat. Say "information is missing on" followed by the related topic, if the given context do not provide sufficient information.

Please cite the contexts with the reference numbers, in the format [citation:x]. If a sentence comes from multiple contexts, please list all applicable citations, like [citation:3][citation:5]. Other than code and specific names and citations, your answer must be written in the same language as the question.

Here are the set of contexts:

{context}

Remember, don't blindly repeat the contexts verbatim. And here is the user question:
"""
# 要使用的停用詞集合 - 這不是一個完整的集合，根據您的觀察，您可能會想要添加更多。
# A set of stop words to use - this is not a complete set, and you may want to
# add more given your observation.
stop_words = [
    "<|im_end|>",
    "[End]",
    "[end]",
    "\nReferences:\n",
    "\nSources:\n",
    "End.",
]
# 這是一個提示，要求模型生成與原始問題及其上下文相關的問題。
# 理想情況下，人們希望同時包括原始問題和模型的答案，但我們在這裡不這樣做：如果我們需要等待答案，那麼生成相關問題通常只能在整個答案生成之後才開始。
# 這會在響應時間上創造明顯的延遲。因此，正如您將在代碼中看到的，我們將向模型發送兩個連續的請求：一個用於獲取答案，另一個用於獲取相關問題。這不是理想的做法，但它是響應時間和質量之間的一個良好妥協。
# This is the prompt that asks the model to generate related questions to the
# original question and the contexts.
# Ideally, one want to include both the original question and the answer from the
# model, but we are not doing that here: if we need to wait for the answer, then
# the generation of the related questions will usually have to start only after
# the whole answer is generated. This creates a noticeable delay in the response
# time. As a result, and as you will see in the code, we will be sending out two
# consecutive requests to the model: one for the answer, and one for the related
# questions. This is not ideal, but it is a good tradeoff between response time
# and quality.
_more_questions_prompt = """
You are a helpful assistant that helps the user to ask related questions, based on user's original question and the related contexts. Please identify worthwhile topics that can be follow-ups, and write questions no longer than 20 words each. Please make sure that specifics, like events, names, locations, are included in follow up questions so they can be asked standalone. For example, if the original question asks about "the Manhattan project", in the follow up question, do not just say "the project", but use the full name "the Manhattan project". Your related questions must be in the same language as the original question.

Here are the contexts of the question:

{context}

Remember, based on the original question and related contexts, suggest three such further questions. Do NOT repeat the original question. Each related question should be no longer than 20 words. Here is the original question:
"""


def search_with_bing(query: str, subscription_key: str):
    """
    Search with bing and return the contexts.
    """
    # 定義函數search_with_bing，接受搜索查詢（query）和Bing訂閱鍵（subscription_key）作為參數。

    params = {"q": query, "mkt": BING_MKT}
    # 設置請求參數，包括查詢字串和市場設定（BING_MKT是一個先前定義的常量，表示查詢的市場）。

    response = requests.get(
        BING_SEARCH_V7_ENDPOINT,
        headers={"Ocp-Apim-Subscription-Key": subscription_key},
        params=params,
        timeout=DEFAULT_SEARCH_ENGINE_TIMEOUT,
    )
    # 使用requests庫發送GET請求到Bing搜索API端點（BING_SEARCH_V7_ENDPOINT），包含訂閱鍵和查詢參數。
    # 設定超時為DEFAULT_SEARCH_ENGINE_TIMEOUT。
    if not response.ok:
        logger.error(f"{response.status_code} {response.text}")
        raise HTTPException(response.status_code, "Search engine error.")
    # 檢查HTTP響應是否成功。如果不成功，記錄錯誤並拋出HTTPException。
    
    json_content = response.json()# 將響應內容解析為JSON格式。
    try:
        contexts = json_content["webPages"]["value"][:REFERENCE_COUNT]
        # 從JSON響應中提取搜索結果，並根據REFERENCE_COUNT限制結果的數量。
    except KeyError:
        logger.error(f"Error encountered: {json_content}")
        # 如果在解析過程中遇到KeyError，記錄錯誤信息。
        return []
        # 發生錯誤時返回一個空列表。
    return contexts
    # 返回提取的上下文列表。

def search_with_google(query: str, subscription_key: str, cx: str):
    """
    Search with google and return the contexts.
    """
    # 定義函數search_with_google，接受搜索查詢（query）、Google API訂閱鍵（subscription_key）和自定義搜索引擎ID（cx）作為參數。
    params = {
        "key": subscription_key,
        "cx": cx,
        "q": query,
        "num": REFERENCE_COUNT,
    }
    # 設置請求參數，包括API鍵、自定義搜索引擎ID、查詢字串以及要返回的結果數量（REFERENCE_COUNT是先前定義的常量）。

    response = requests.get(
        GOOGLE_SEARCH_ENDPOINT, params=params, timeout=DEFAULT_SEARCH_ENGINE_TIMEOUT
    )
    # 使用requests庫發送GET請求到Google自定義搜索API端點（GOOGLE_SEARCH_ENDPOINT），包含上面定義的請求參數。
    # 設定超時為DEFAULT_SEARCH_ENGINE_TIMEOUT。

    if not response.ok:
        logger.error(f"{response.status_code} {response.text}")
        raise HTTPException(response.status_code, "Search engine error.")
    # 檢查HTTP響應是否成功。如果不成功，記錄錯誤並拋出HTTPException。
    json_content = response.json()# 將響應內容解析為JSON格式。
    try:
        contexts = json_content["items"][:REFERENCE_COUNT]
        # 從JSON響應中提取搜索結果，並根據REFERENCE_COUNT限制結果的數量。
    except KeyError:
        logger.error(f"Error encountered: {json_content}")
        # 如果在解析過程中遇到KeyError，記錄錯誤信息。
        return []
        # 發生錯誤時返回一個空列表。
    return contexts
    # 返回提取的上下文列表。


def search_with_serper(query: str, subscription_key: str):
    """
    Search with serper and return the contexts.
    """
    # 定義函數search_with_serper，接受搜索查詢（query）和Serper訂閱鍵（subscription_key）作為參數。

    payload = json.dumps({
        "q": query,
        "num": (
            REFERENCE_COUNT
            if REFERENCE_COUNT % 10 == 0
            else (REFERENCE_COUNT // 10 + 1) * 10
        ),
    })
    # 構造請求負載，包括查詢字串和結果數量。結果數量根據REFERENCE_COUNT計算以符合Serper API的要求。

    headers = {"X-API-KEY": subscription_key, "Content-Type": "application/json"}
    # 設置HTTP請求頭部，包括訂閱鍵和指定內容類型為JSON。
    
    logger.info(
        f"{payload} {headers} {subscription_key} {query} {SERPER_SEARCH_ENDPOINT}"
    )
    # 使用logger記錄請求的詳細信息，方便調試。
    response = requests.post(
        SERPER_SEARCH_ENDPOINT,
        headers=headers,
        data=payload,
        timeout=DEFAULT_SEARCH_ENGINE_TIMEOUT,
    )
    # 使用requests庫發送POST請求到Serper搜索API端點（SERPER_SEARCH_ENDPOINT），包含頭部、負載和超時設定。

    if not response.ok:
        logger.error(f"{response.status_code} {response.text}")
        raise HTTPException(response.status_code, "Search engine error.")
    # 檢查HTTP響應是否成功。如果不成功，記錄錯誤並拋出HTTPException。
    
    json_content = response.json()# 將響應內容解析為JSON格式。
    try:
        # convert to the same format as bing/google
        # 轉換成與Bing/Google相同的格式
        contexts = []
        # 初始化上下文列表。
        # 處理知識圖谱部分的數據。
        if json_content.get("knowledgeGraph"):
            url = json_content["knowledgeGraph"].get("descriptionUrl") or json_content["knowledgeGraph"].get("website")
            snippet = json_content["knowledgeGraph"].get("description")
            if url and snippet:
                contexts.append({
                    "name": json_content["knowledgeGraph"].get("title",""),
                    "url": url,
                    "snippet": snippet
                })
        # 處理答案框部分的數據。
        if json_content.get("answerBox"):
            url = json_content["answerBox"].get("url")
            snippet = json_content["answerBox"].get("snippet") or json_content["answerBox"].get("answer")
            if url and snippet:
                contexts.append({
                    "name": json_content["answerBox"].get("title",""),
                    "url": url,
                    "snippet": snippet
                })
        # 處理有機搜索結果部分的數據。
        contexts += [
            {"name": c["title"], "url": c["link"], "snippet": c.get("snippet","")}
            for c in json_content["organic"]
        ]
         # 返回根據REFERENCE_COUNT限制的上下文列表。
        return contexts[:REFERENCE_COUNT]
    except KeyError:
        logger.error(f"Error encountered: {json_content}")
        return []
        # 如果在解析過程中遇到KeyError，記錄錯誤信息並返回一個空列表。

def search_with_searchapi(query: str, subscription_key: str):
    """
    Search with SearchApi.io and return the contexts.
    """
    payload = {
        "q": query,
        "engine": "google",
        "num": (
            REFERENCE_COUNT
            if REFERENCE_COUNT % 10 == 0
            else (REFERENCE_COUNT // 10 + 1) * 10
        ),
    }
    headers = {"Authorization": f"Bearer {subscription_key}", "Content-Type": "application/json"}
    logger.info(
        f"{payload} {headers} {subscription_key} {query} {SEARCHAPI_SEARCH_ENDPOINT}"
    )
    response = requests.get(
        SEARCHAPI_SEARCH_ENDPOINT,
        headers=headers,
        params=payload,
        timeout=30,
    )
    if not response.ok:
        logger.error(f"{response.status_code} {response.text}")
        raise HTTPException(response.status_code, "Search engine error.")
    json_content = response.json()
    try:
        # convert to the same format as bing/google
        contexts = []

        if json_content.get("answer_box"):
            if json_content["answer_box"].get("organic_result"):
                title = json_content["answer_box"].get("organic_result").get("title", "")
                url = json_content["answer_box"].get("organic_result").get("link", "")
            if json_content["answer_box"].get("type") == "population_graph":
                title = json_content["answer_box"].get("place", "")
                url = json_content["answer_box"].get("explore_more_link", "")

            title = json_content["answer_box"].get("title", "")
            url = json_content["answer_box"].get("link")
            snippet =  json_content["answer_box"].get("answer") or json_content["answer_box"].get("snippet")

            if url and snippet:
                contexts.append({
                    "name": title,
                    "url": url,
                    "snippet": snippet
                })

        if json_content.get("knowledge_graph"):
            if json_content["knowledge_graph"].get("source"):
                url = json_content["knowledge_graph"].get("source").get("link", "")

            url = json_content["knowledge_graph"].get("website", "")
            snippet = json_content["knowledge_graph"].get("description")

            if url and snippet:
                contexts.append({
                    "name": json_content["knowledge_graph"].get("title", ""),
                    "url": url,
                    "snippet": snippet
                })

        contexts += [
            {"name": c["title"], "url": c["link"], "snippet": c.get("snippet", "")}
            for c in json_content["organic_results"]
        ]
        
        if json_content.get("related_questions"):
            for question in json_content["related_questions"]:
                if question.get("source"):
                    url = question.get("source").get("link", "")
                else:
                    url = ""  
                    
                snippet = question.get("answer", "")

                if url and snippet:
                    contexts.append({
                        "name": question.get("question", ""),
                        "url": url,
                        "snippet": snippet
                    })

        return contexts[:REFERENCE_COUNT]
    except KeyError:
        logger.error(f"Error encountered: {json_content}")
        return []

class RAG(Photon):
    """
    Retrieval-Augmented Generation Demo from Lepton AI.

    This is a minimal example to show how to build a RAG engine with Lepton AI.
    It uses search engine to obtain results based on user queries, and then uses
    LLM models to generate the answer as well as related questions. The results
    are then stored in a KV so that it can be retrieved later.
    """

    requirement_dependency = [
        "openai",  # for openai client usage.
    ]

    extra_files = glob.glob("ui/**/*", recursive=True)

    deployment_template = {
        # All actual computations are carried out via remote apis, so
        # we will use a cpu.small instance which is already enough for most of
        # the work.
        "resource_shape": "cpu.small",
        # You most likely don't need to change this.
        "env": {
            # Choose the backend. Currently, we support BING and GOOGLE. For
            # simplicity, in this demo, if you specify the backend as LEPTON,
            # we will use the hosted serverless version of lepton search api
            # at https://search-api.lepton.run/ to do the search and RAG, which
            # runs the same code (slightly modified and might contain improvements)
            # as this demo.
            "BACKEND": "LEPTON",
            # If you are using google, specify the search cx.
            "GOOGLE_SEARCH_CX": "",
            # Specify the LLM model you are going to use.
            "LLM_MODEL": "mixtral-8x7b",
            # For all the search queries and results, we will use the Lepton KV to
            # store them so that we can retrieve them later. Specify the name of the
            # KV here.
            "KV_NAME": "search-with-lepton",
            # If set to true, will generate related questions. Otherwise, will not.
            "RELATED_QUESTIONS": "true",
            # On the lepton platform, allow web access when you are logged in.
            "LEPTON_ENABLE_AUTH_BY_COOKIE": "true",
        },
        # Secrets you need to have: search api subscription key, and lepton
        # workspace token to query lepton's llama models.
        "secret": [
            # If you use BING, you need to specify the subscription key. Otherwise
            # it is not needed.
            "BING_SEARCH_V7_SUBSCRIPTION_KEY",
            # If you use GOOGLE, you need to specify the search api key. Note that
            # you should also specify the cx in the env.
            "GOOGLE_SEARCH_API_KEY",
            # If you use Serper, you need to specify the search api key.
            "SERPER_SEARCH_API_KEY",
            # If you use SearchApi, you need to specify the search api key.
            "SEARCHAPI_API_KEY",
            # You need to specify the workspace token to query lepton's LLM models.
            "LEPTON_WORKSPACE_TOKEN",
        ],
    }

    # It's just a bunch of api calls, so our own deployment can be made massively
    # concurrent.
    handler_max_concurrency = 16

    def local_client(self):
        """
        Gets a thread-local client, so in case openai clients are not thread safe,
        each thread will have its own client.
        """
        import openai

        thread_local = threading.local()
        try:
            return thread_local.client
        except AttributeError:
            thread_local.client = openai.OpenAI(
                base_url=f"https://{self.model}.lepton.run/api/v1/",
                api_key=os.environ.get("LEPTON_WORKSPACE_TOKEN")
                or WorkspaceInfoLocalRecord.get_current_workspace_token(),
                # We will set the connect timeout to be 10 seconds, and read/write
                # timeout to be 120 seconds, in case the inference server is
                # overloaded.
                timeout=httpx.Timeout(connect=10, read=120, write=120, pool=10),
            )
            return thread_local.client

    def init(self):
        """
        Initializes photon configs.
        """
        # First, log in to the workspace.
        leptonai.api.workspace.login()
        self.backend = os.environ["BACKEND"].upper()
        if self.backend == "LEPTON":
            self.leptonsearch_client = Client(
                "https://search-api.lepton.run/",
                token=os.environ.get("LEPTON_WORKSPACE_TOKEN")
                or WorkspaceInfoLocalRecord.get_current_workspace_token(),
                stream=True,
                timeout=httpx.Timeout(connect=10, read=120, write=120, pool=10),
            )
        elif self.backend == "BING":
            self.search_api_key = os.environ["BING_SEARCH_V7_SUBSCRIPTION_KEY"]
            self.search_function = lambda query: search_with_bing(
                query,
                self.search_api_key,
            )
        elif self.backend == "GOOGLE":
            self.search_api_key = os.environ["GOOGLE_SEARCH_API_KEY"]
            self.search_function = lambda query: search_with_google(
                query,
                self.search_api_key,
                os.environ["GOOGLE_SEARCH_CX"],
            )
        elif self.backend == "SERPER":
            self.search_api_key = os.environ["SERPER_SEARCH_API_KEY"]
            self.search_function = lambda query: search_with_serper(
                query,
                self.search_api_key,
            )
        elif self.backend == "SEARCHAPI":
            self.search_api_key = os.environ["SEARCHAPI_API_KEY"]
            self.search_function = lambda query: search_with_searchapi(
                query,
                self.search_api_key,
            )
        else:
            raise RuntimeError("Backend must be LEPTON, BING, GOOGLE, SERPER or SEARCHAPI.")
        self.model = os.environ["LLM_MODEL"]
        # An executor to carry out async tasks, such as uploading to KV.
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.handler_max_concurrency * 2
        )
        # Create the KV to store the search results.
        logger.info("Creating KV. May take a while for the first time.")
        self.kv = KV(
            os.environ["KV_NAME"], create_if_not_exists=True, error_if_exists=False
        )
        # whether we should generate related questions.
        self.should_do_related_questions = to_bool(os.environ["RELATED_QUESTIONS"])

    def get_related_questions(self, query, contexts):
        """
        Gets related questions based on the query and context.
        """

        def ask_related_questions(
            questions: Annotated[
                List[str],
                [(
                    "question",
                    Annotated[
                        str, "related question to the original question and context."
                    ],
                )],
            ]
        ):
            """
            ask further questions that are related to the input and output.
            """
            pass

        try:
            response = self.local_client().chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": _more_questions_prompt.format(
                            context="\n\n".join([c["snippet"] for c in contexts])
                        ),
                    },
                    {
                        "role": "user",
                        "content": query,
                    },
                ],
                tools=[{
                    "type": "function",
                    "function": tool.get_tools_spec(ask_related_questions),
                }],
                max_tokens=512,
            )
            related = response.choices[0].message.tool_calls[0].function.arguments
            if isinstance(related, str):
                related = json.loads(related)
            logger.trace(f"Related questions: {related}")
            return related["questions"][:5]
        except Exception as e:
            # For any exceptions, we will just return an empty list.
            logger.error(
                "encountered error while generating related questions:"
                f" {e}\n{traceback.format_exc()}"
            )
            return []

    def _raw_stream_response(
        self, contexts, llm_response, related_questions_future
    ) -> Generator[str, None, None]:
        """
        A generator that yields the raw stream response. You do not need to call
        this directly. Instead, use the stream_and_upload_to_kv which will also
        upload the response to KV.
        """
        # First, yield the contexts.
        yield json.dumps(contexts)
        yield "\n\n__LLM_RESPONSE__\n\n"
        # Second, yield the llm response.
        if not contexts:
            # Prepend a warning to the user
            yield (
                "(The search engine returned nothing for this query. Please take the"
                " answer with a grain of salt.)\n\n"
            )
        for chunk in llm_response:
            if chunk.choices:
                yield chunk.choices[0].delta.content or ""
        # Third, yield the related questions. If any error happens, we will just
        # return an empty list.
        if related_questions_future is not None:
            related_questions = related_questions_future.result()
            try:
                result = json.dumps(related_questions)
            except Exception as e:
                logger.error(f"encountered error: {e}\n{traceback.format_exc()}")
                result = "[]"
            yield "\n\n__RELATED_QUESTIONS__\n\n"
            yield result

    def stream_and_upload_to_kv(
        self, contexts, llm_response, related_questions_future, search_uuid
    ) -> Generator[str, None, None]:
        """
        Streams the result and uploads to KV.
        """
        # First, stream and yield the results.
        all_yielded_results = []
        for result in self._raw_stream_response(
            contexts, llm_response, related_questions_future
        ):
            all_yielded_results.append(result)
            yield result
        # Second, upload to KV. Note that if uploading to KV fails, we will silently
        # ignore it, because we don't want to affect the user experience.
        _ = self.executor.submit(self.kv.put, search_uuid, "".join(all_yielded_results))

    @Photon.handler(method="POST", path="/query")
    def query_function(
        self,
        query: str,
        search_uuid: str,
        generate_related_questions: Optional[bool] = True,
    ) -> StreamingResponse:
        """
        Query the search engine and returns the response.

        The query can have the following fields:
            - query: the user query.
            - search_uuid: a uuid that is used to store or retrieve the search result. If
                the uuid does not exist, generate and write to the kv. If the kv
                fails, we generate regardless, in favor of availability. If the uuid
                exists, return the stored result.
            - generate_related_questions: if set to false, will not generate related
                questions. Otherwise, will depend on the environment variable
                RELATED_QUESTIONS. Default: true.
        """
        # Note that, if uuid exists, we don't check if the stored query is the same
        # as the current query, and simply return the stored result. This is to enable
        # the user to share a searched link to others and have others see the same result.
        if search_uuid:
            try:
                result = self.kv.get(search_uuid)

                def str_to_generator(result: str) -> Generator[str, None, None]:
                    yield result

                return StreamingResponse(str_to_generator(result))
            except KeyError:
                logger.info(f"Key {search_uuid} not found, will generate again.")
            except Exception as e:
                logger.error(
                    f"KV error: {e}\n{traceback.format_exc()}, will generate again."
                )
        else:
            raise HTTPException(status_code=400, detail="search_uuid must be provided.")

        if self.backend == "LEPTON":
            # delegate to the lepton search api.
            result = self.leptonsearch_client.query(
                query=query,
                search_uuid=search_uuid,
                generate_related_questions=generate_related_questions,
            )
            return StreamingResponse(content=result, media_type="text/html")

        # First, do a search query.
        query = query or _default_query
        # Basic attack protection: remove "[INST]" or "[/INST]" from the query
        query = re.sub(r"\[/?INST\]", "", query)
        contexts = self.search_function(query)

        system_prompt = _rag_query_text.format(
            context="\n\n".join(
                [f"[[citation:{i+1}]] {c['snippet']}" for i, c in enumerate(contexts)]
            )
        )
        try:
            client = self.local_client()
            llm_response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query},
                ],
                max_tokens=1024,
                stop=stop_words,
                stream=True,
                temperature=0.9,
            )
            if self.should_do_related_questions and generate_related_questions:
                # While the answer is being generated, we can start generating
                # related questions as a future.
                related_questions_future = self.executor.submit(
                    self.get_related_questions, query, contexts
                )
            else:
                related_questions_future = None
        except Exception as e:
            logger.error(f"encountered error: {e}\n{traceback.format_exc()}")
            return HTMLResponse("Internal server error.", 503)

        return StreamingResponse(
            self.stream_and_upload_to_kv(
                contexts, llm_response, related_questions_future, search_uuid
            ),
            media_type="text/html",
        )

    @Photon.handler(mount=True)
    def ui(self):
        return StaticFiles(directory="ui")

    @Photon.handler(method="GET", path="/")
    def index(self) -> RedirectResponse:
        """
        Redirects "/" to the ui page.
        """
        return RedirectResponse(url="/ui/index.html")


if __name__ == "__main__":
    rag = RAG()
    rag.launch()
