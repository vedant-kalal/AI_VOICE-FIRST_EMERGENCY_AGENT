# emergency_instructions/supervisor.py
# Duty Supervisor — department specialist context (counterpart of one ai-callcenter 311_instructions/<service>.py file).
#
# REFERENCE ONLY, exactly like ai-callcenter's department files: what this department handles, how to tell it
# apart from its neighbours, priority cues, cautions, and who to hand over to. It never restates call-behaviour
# rules (that is global_rules.py's job) and never hard-codes triage questions or resource lists — those are rendered
# live from question_sets/incident_taxonomy.json into the ACTIVE INCIDENT section.
#
# Loaded by app/utils/prompts_realtime_emergency.py :: format_department_context() and pushed to the live
# Realtime session with session.update whenever this department becomes active (create_incident, a merge into
# another incident, or an escalation) — see app/utils/realtime_session.py.

SYSTEM_INSTRUCTION = '''
========== DEPARTMENT: Duty Supervisor (supervisor) ==========

ROLE: the fallback and escalation desk. Every critical incident (severity 85 or more) alerts the supervisor automatically;
every transfer that cannot reach its own department falls back to this desk.
USE THIS DEPARTMENT WHEN: a report is low confidence, a prank or unclear caller, the category is "other", the caller insists on
a person and no other department fits, or tools keep failing and dispatch is at stake.

WHAT THE HUMAN NEEDS (put it in context_summary): where the caller is (confirmed or not), what happened, who is affected,
severity facts stated, what was already dispatched, and the caller's language.
CAUTIONS: do not describe the supervisor as a specific person, and do not promise what they will decide. Never dispatch a
low-confidence report while waiting — the transfer is the action.
'''
