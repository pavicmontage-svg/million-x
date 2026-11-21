package com.titan.copyai.data.db

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import kotlinx.coroutines.flow.Flow

/** Offline cache: last known server responses (shown with their timestamp when offline). */
@Entity(tableName = "cache")
data class CacheEntry(@PrimaryKey val key: String, val json: String, val updatedAt: Long)

@Entity(tableName = "notifications")
data class NotificationEntity(
    @PrimaryKey val id: Long,
    val ts: String,
    val kind: String,
    val severity: String,
    val title: String,
    val body: String,
    val read: Boolean,
)

@Dao
interface CacheDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun put(e: CacheEntry)
    @Query("SELECT * FROM cache WHERE `key` = :key") suspend fun get(key: String): CacheEntry?
    @Query("DELETE FROM cache") suspend fun clear()
}

@Dao
interface NotificationDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun upsert(items: List<NotificationEntity>)
    @Query("SELECT * FROM notifications ORDER BY id DESC LIMIT 300") fun observe(): Flow<List<NotificationEntity>>
    @Query("SELECT MAX(id) FROM notifications") suspend fun maxId(): Long?
    @Query("UPDATE notifications SET read = 1 WHERE id IN (:ids)") suspend fun markRead(ids: List<Long>)
    @Query("DELETE FROM notifications") suspend fun clear()
}

@Database(entities = [CacheEntry::class, NotificationEntity::class], version = 1, exportSchema = true)
abstract class TitanDatabase : RoomDatabase() {
    abstract fun cache(): CacheDao
    abstract fun notifications(): NotificationDao

    companion object {
        fun build(context: Context): TitanDatabase =
            Room.databaseBuilder(context, TitanDatabase::class.java, "titan.db").fallbackToDestructiveMigration().build()
    }
}
