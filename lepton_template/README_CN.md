
# Lepton 搜索

使用不到 500 行的代碼建立您自己的對話搜索引擎。

在這裡看到一個實時演示網站 https://search.lepton.run/

這個項目的源代碼在\[這裡\](https://github.com/leptonai/search_with_lepton/)。README 將詳細說明如何在 Lepton 的平台上設置和部署這個項目。

## 設置搜索引擎 API

您有幾個選擇來設置您的搜索引擎 API。您可以使用 Bing 或 Google，或者如果您只是想快速試試演示，直接使用 lepton 演示 API。

### Bing

如果您使用 Bing，您可以在這裡訂閱 bing 搜索 api。之後，記下 Bing 搜索 api 訂閱鑰匙。我們遵循慣例，將其命名為 BING_SEARCH_V7_SUBSCRIPTION_KEY。我們建議您將鑰匙作為秘密存儲在 Lepton 中。

### Google

如果您選擇使用 Google，您可以按照這裡的指示獲取您的 Google 搜索 api 鑰匙。我們遵循慣例，將其命名為 GOOGLE_SEARCH_API_KEY。我們建議您將鑰匙作為秘密存儲在 Lepton 中。您還將獲得一個搜索引擎 CX id，您也需要它。

### SearchApi

如果您想使用 SearchApi，一個第三方 Google 搜索 API，您可以通過在這裡註冊來檢索 API 鑰匙。我們遵循慣例，將其命名為 SEARCHAPI_API_KEY。我們建議您將鑰匙作為秘密存儲在 Lepton 中。

### Lepton 演示 API

如果您選擇使用 lepton 演示 api，您不需要做任何事情 - 您的工作區憑證將給您訪問演示 api 的權限。請注意，這將產生 API 調用成本。

### 部署配置

這裡是您可以為您的部署設置的配置：

* 名稱：您部署的名稱，如 "my-search"
* 資源形狀：大部分的重工作將由 LLM 服務器和搜索引擎 API 完成，所以您可以選擇一個小資源形狀。cpu.small 通常就足夠了。
然後，設置以下環境變量。

* BACKEND: 使用的搜索後端。如果您沒有設置 bing 或 google，簡單地使用 LEPTON 試試演示。否則，選擇 BING、GOOGLE 或 SEARCHAPI。
* LLM_MODEL: 運行的 LLM 模型。我們推薦使用 mixtral-8x7b，但如果您想試驗其他模型，您可以嘗試 LeptonAI 上托管的模型，例如，llama2-70b, llama2-13b, * * llama2-7b。請注意，小型模型可能不會運行得很好。
* KV_NAME: 用於存儲搜索結果的 Lepton KV。您可以使用默認的 search-with-lepton。
* RELATED_QUESTIONS: 是否生成相關問題。如果您將此設置為 true，搜索引擎將為您生成相關問題。否則，它將不會。
* GOOGLE_SEARCH_CX: 如果您使用 google，請指定搜索 cx。否則，留空。
* LEPTON_ENABLE_AUTH_BY_COOKIE: 這是允許網頁 UI 訪問部署的。設置為 true。
  
此外，您需要設置以下密鑰：

* LEPTON_WORKSPACE_TOKEN: 調用 Lepton 的 LLM 和 KV apis 需要這個。您可以在設置中找到您的工作區令牌。
* BING_SEARCH_V7_SUBSCRIPTION_KEY: 如果您使用 Bing，您需要指定訂閱鑰匙。否則不需要。
* GOOGLE_SEARCH_API_KEY: 如果您使用 Google，您需要指定搜索 api 鑰匙。注意您也應該在 env 中指定 cx。如果您不使用 Google，則不需要。
* SEARCHAPI_API_KEY: 如果您使用 SearchApi，一個第三方 Google 搜索 API，您需要指定 api 鑰匙。
  
一旦這些欄位設置好，點擊頁面底部的部署按鈕來創建部署。您可以看到部署現在已在部署下創建。點擊部署名稱檢查細節。您將能在這頁面上看到部署 URL 和狀態。

一旦狀態變為就緒，點擊部署卡上的 URL 來訪問它。

