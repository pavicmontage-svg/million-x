package com.titan.copyai

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Launches the real app: first start shows onboarding; once the on-phone engine was started it opens the dashboard. */
@RunWith(AndroidJUnit4::class)
class LaunchTest {
    @get:Rule val rule = createAndroidComposeRule<MainActivity>()

    @Test fun launchesIntoOnboardingOrDashboard() {
        val paired = (rule.activity.application as TitanApp).repo.store.isPaired
        rule.waitForIdle()
        if (paired) {
            rule.waitUntil(30_000) { rule.onAllNodes(androidx.compose.ui.test.hasText("Main Dashboard")).fetchSemanticsNodes().isNotEmpty() }
            rule.onNodeWithText("Main Dashboard").assertIsDisplayed()
        } else {
            rule.onNodeWithText("TITAN COPY AI").assertIsDisplayed()
            rule.onNodeWithText("Start engine on this phone").assertIsDisplayed()
        }
    }
}
