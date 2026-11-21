package com.titan.copyai.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.titan.copyai.data.Cached
import com.titan.copyai.data.Repository
import com.titan.copyai.ui.components.ErrorBox
import com.titan.copyai.ui.components.Loading
import com.titan.copyai.ui.components.StaleBanner
import com.titan.copyai.ui.nav.ContentPadding
import com.titan.copyai.ui.nav.LocalCtx
import com.titan.copyai.ui.vm.LoadVm
import com.titan.copyai.ui.vm.UiState

@Composable
fun <T> rememberLoad(key: String, loader: suspend Repository.() -> Cached<T>): LoadVm<T> {
    val repo = LocalCtx.current.app.repo
    return viewModel(key = key) { LoadVm { repo.loader() } }
}

/** Standard list screen: pull-to-refresh, live auto-refresh, offline banner, error/loading states. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun <T> LoadedList(vm: LoadVm<T>, autoRefresh: Boolean = true, content: LazyListScope.(T) -> Unit) {
    val ctx = LocalCtx.current
    val state by vm.state.collectAsState()
    LaunchedEffect(vm) { vm.messages.collect { ctx.snack(it) } }
    LaunchedEffect(ctx.tick) { if (autoRefresh && ctx.tick > 0) vm.refresh(silent = true) }
    when (val s = state) {
        is UiState.Loading -> Loading()
        is UiState.Error -> LazyColumn(contentPadding = ContentPadding) { item { ErrorBox(s.message) { vm.refresh() } } }
        is UiState.Data -> PullToRefreshBox(isRefreshing = false, onRefresh = { vm.refresh() }) {
            LazyColumn(contentPadding = ContentPadding, verticalArrangement = Arrangement.spacedBy(10.dp)) {
                if (s.stale) item { StaleBanner(s.updatedAt, s.error) }
                content(s.value)
            }
        }
    }
}
