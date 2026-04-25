"""Tests for conversation prompt generation."""

from app.conversation.prompts import get_system_prompt


class TestGetSystemPrompt:
    def test_check_in_prompt(self):
        prompt = get_system_prompt("check_in", due_meds=[], patient_name="Dad")
        assert "Dad" in prompt
        assert "morning" in prompt.lower() or "check" in prompt.lower()

    def test_med_reminder_prompt_includes_meds(self):
        meds = [{"name": "Levodopa", "dosage": "100mg", "time": "08:00"}]
        prompt = get_system_prompt("med_reminder", due_meds=meds, patient_name="Dad")
        assert "Levodopa" in prompt
        assert "100mg" in prompt

    def test_evening_chat_prompt(self):
        prompt = get_system_prompt("evening_chat", due_meds=[], patient_name="Dad")
        assert "Dad" in prompt
        assert "evening" in prompt.lower() or "day" in prompt.lower()

    def test_prompt_is_warm_not_robotic(self):
        prompt = get_system_prompt("check_in", due_meds=[], patient_name="Dad")
        # Should not contain cold clinical language
        assert "SYSTEM:" not in prompt
        assert "ERROR" not in prompt
        # Should contain warm companion language
        assert len(prompt) > 100  # substantial prompt

    def test_unknown_call_type_falls_back(self):
        prompt = get_system_prompt("unknown_type", due_meds=[], patient_name="Dad")
        assert "Dad" in prompt
        assert len(prompt) > 50
