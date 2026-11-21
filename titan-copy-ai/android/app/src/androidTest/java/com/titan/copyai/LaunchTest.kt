package com.titan.copyai

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Launches the real app on an emulator: unpaired devices must land on the pairing screen. */
@RunWith(AndroidJUnit4::class)
class LaunchTest {
    @get:Rule val rule = createAndroidComposeRule<MainActivity>()

    @Test fun showsPairingScreenAndRequiresCode() {
        rule.onNodeWithText("TITAN COPY AI").assertIsDisplayed()
        rule.onNodeWithText("Connect to your server".uppercase()).assertIsDisplayed()
        rule.onNodeWithText("Pair device").assertIsNotEnabled()
    }
}
