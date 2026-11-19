package com.millionx.ai

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.millionx.ai.ui.components.EmergencyButton
import com.millionx.ai.ui.components.LoadBox
import com.millionx.ai.ui.components.ModeRibbon
import com.millionx.ai.ui.screens.PairingScreen
import com.millionx.ai.ui.theme.MillionXTheme
import com.millionx.ai.ui.vm.HealthVM
import com.millionx.ai.ui.vm.Load
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class UiSafetyTest {
    @get:Rule val rule = createComposeRule()

    @Before fun clean() {
        MillionXApp.instance.store.clear()
    }

    @Test fun emergencyStopAsksForConfirmationThenFires() {
        var fired = 0
        rule.setContent { MillionXTheme { EmergencyButton { fired++ } } }
        rule.onNodeWithText("EMERGENCY STOP").performClick()
        rule.onNodeWithText("STOP ALL").assertIsDisplayed()
        assertEquals(0, fired)
        rule.onNodeWithText("STOP ALL").performClick()
        assertEquals(1, fired)
    }

    @Test fun ribbonShowsUnreachableServerAndKeepsEmergencyAccess() {
        rule.setContent { MillionXTheme { ModeRibbon(HealthVM()) } }
        rule.waitUntil(5_000) { rule.onAllNodes(hasText("SERVER UNREACHABLE", substring = true)).fetchSemanticsNodes().isNotEmpty() }
        rule.onNodeWithTag("ribbon_emergency").assertIsDisplayed().performClick()
        rule.onNodeWithText("STOP ALL").assertIsDisplayed()
    }

    @Test fun pairingRequiresACode() {
        rule.setContent { MillionXTheme { PairingScreen {} } }
        rule.onNodeWithText("Pair device").assertIsNotEnabled()
        rule.onNodeWithText("Pairing code").performTextInput("ABCD-1234-EF01")
        rule.onNodeWithText("Pair device").assertIsEnabled()
    }

    @Test fun loadErrorsOfferRetry() {
        var retried = 0
        rule.setContent { MillionXTheme { LoadBox<String>(Load.Error("boom"), { retried++ }) {} } }
        rule.onNodeWithText("boom").assertIsDisplayed()
        rule.onNodeWithText(" Retry").performClick()
        assertEquals(1, retried)
    }
}
