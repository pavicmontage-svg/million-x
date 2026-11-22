package com.titan.copyai.data.api

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import okhttp3.MultipartBody
import okhttp3.RequestBody
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.Multipart
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.Part
import retrofit2.http.Path
import retrofit2.http.Query

interface ApiService {
    @POST("api/pair") suspend fun pair(@Body body: PairRequest): PairResponse
    @GET("api/health") suspend fun health(): JsonObject

    @GET("api/dashboard") suspend fun dashboard(): Dashboard
    @GET("api/portfolios") suspend fun portfolios(): List<JsonObject>
    @PATCH("api/portfolios/{id}") suspend fun patchPortfolio(@Path("id") id: Int, @Body body: JsonObject): JsonObject
    @POST("api/portfolios/{id}/reactivate") suspend fun reactivate(@Path("id") id: Int, @Body body: JsonObject): JsonObject
    @GET("api/portfolios/{id}/equity") suspend fun equity(@Path("id") id: Int, @Query("days") days: Int = 30): List<EquityPoint>

    @GET("api/traders") suspend fun traders(@Query("market") market: String? = null): List<TraderRow>
    @GET("api/traders/{id}") suspend fun trader(@Path("id") id: Int): TraderDetail
    @POST("api/traders/{id}/status") suspend fun traderStatus(@Path("id") id: Int, @Body body: JsonObject): JsonObject
    @POST("api/traders/{id}/signal-source") suspend fun signalSource(@Path("id") id: Int): JsonObject
    @POST("api/traders/{id}/source-account") suspend fun sourceAccount(@Path("id") id: Int, @Body body: JsonObject): JsonObject
    @DELETE("api/traders/{id}/source-account") suspend fun removeSourceAccount(@Path("id") id: Int): JsonObject
    @Multipart @POST("api/traders/import")
    suspend fun importTrader(@Part("name") name: RequestBody, @Part("platform") platform: RequestBody,
                             @Part("market") market: RequestBody, @Part trades: MultipartBody.Part?): JsonObject

    @GET("api/allocations") suspend fun allocations(): List<AllocationDto>
    @POST("api/allocations") suspend fun upsertAllocation(@Body body: AllocationIn): JsonObject

    @GET("api/positions") suspend fun positions(@Query("status") status: String = "open",
                                                @Query("portfolio_id") portfolioId: Int? = null,
                                                @Query("limit") limit: Int = 200): List<PositionDto>
    @POST("api/positions/{id}/close") suspend fun closePosition(@Path("id") id: Long): JsonObject
    @GET("api/signals") suspend fun signals(@Query("limit") limit: Int = 100): List<SignalDto>
    @GET("api/orders") suspend fun orders(@Query("limit") limit: Int = 100): List<JsonObject>

    @GET("api/risk") suspend fun risk(): JsonObject
    @GET("api/agents") suspend fun agents(): List<AgentDto>
    @POST("api/agents/{name}/run") suspend fun runAgent(@Path("name") name: String, @Body body: JsonObject): JsonObject
    @GET("api/analytics/{id}") suspend fun analytics(@Path("id") traderId: Int): JsonObject
    @POST("api/backtest") suspend fun backtest(@Body body: JsonObject): JsonObject
    @GET("api/readiness") suspend fun readiness(): List<JsonObject>

    @GET("api/connections") suspend fun connections(): List<ConnectionDto>
    @POST("api/connections") suspend fun addConnection(@Body body: CredentialIn): JsonObject
    @POST("api/connections/{id}/verify") suspend fun verifyConnection(@Path("id") id: Int): JsonObject
    @POST("api/connections/{id}/portfolio") suspend fun attachPortfolio(@Path("id") id: Int, @Body body: JsonObject): JsonObject
    @DELETE("api/connections/{id}") suspend fun deleteConnection(@Path("id") id: Int): JsonObject
    @POST("api/ctrader/oauth/start") suspend fun ctraderStart(@Body body: JsonObject): JsonObject
    @GET("api/ctrader/oauth/{state}") suspend fun ctraderStatus(@Path("state") state: String): JsonObject
    @POST("api/ctrader/oauth/{state}/code") suspend fun ctraderCode(@Path("state") state: String, @Body body: JsonObject): JsonObject
    @POST("api/ctrader/oauth/{state}/finish") suspend fun ctraderFinish(@Path("state") state: String, @Body body: JsonObject): JsonObject

    @GET("api/notifications") suspend fun notifications(@Query("limit") limit: Int = 100,
                                                        @Query("after_id") afterId: Long = 0): List<NotificationDto>
    @POST("api/notifications/read") suspend fun markRead(@Body body: JsonObject): JsonObject
    @GET("api/settings") suspend fun settings(): JsonObject
    @GET("api/audit") suspend fun audit(@Query("limit") limit: Int = 200): List<AuditDto>
    @GET("api/audit/verify") suspend fun auditVerify(): JsonObject

    @GET("api/emergency") suspend fun emergency(): JsonObject
    @POST("api/emergency/stop") suspend fun emergencyStop(@Body body: JsonObject): JsonObject
    @POST("api/emergency/release") suspend fun emergencyRelease(@Body body: JsonObject): JsonObject
    @POST("api/emergency/flatten") suspend fun flatten(@Body body: JsonObject): JsonObject
    @POST("api/emergency/trader/{id}/stop") suspend fun stopTrader(@Path("id") id: Int, @Body body: JsonObject): JsonObject
    @POST("api/live/approve") suspend fun approveLive(@Body body: JsonObject): JsonElement
}
