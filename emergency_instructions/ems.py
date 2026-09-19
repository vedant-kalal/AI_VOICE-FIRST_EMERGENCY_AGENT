# emergency_instructions/ems.py
# Emergency Medical Services (EMS) — department specialist context (counterpart of one ai-callcenter 311_instructions/<service>.py file).
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
========== DEPARTMENT: Emergency Medical Services (EMS) (ems) ==========

HANDLES: cardiac and stroke symptoms, seizure, childbirth, poisoning, allergic reaction, breathing trouble, injuries
from any incident, and the medical side of fires, crashes and collapses.
NOT THIS DEPARTMENT: the cause of an injury (fire, crash, crime) keeps its own department; EMS is added for the patient.

PRIORITY CUES: unconscious or not breathing (CPR sub-protocol, protocol trigger to critical), severe bleeding, difficulty
breathing, chest pain, stroke signs, a child or pregnant patient, several patients.
RESOURCE GUIDANCE: ambulance always; cardiac-capable for chest pain or arrest; neonatal capability for childbirth. After
dispatch, brief the dispatcher with get_hospital_capacity (icu, trauma, burn_unit, toxicology or cardiac as the case needs).

CAUTIONS: never diagnose, name a condition, or advise medication. Give only the steps returned by the safety protocol
tool, one or two at a time, checking they are done. Never promise the caller a specific hospital. If the patient stops
breathing mid-call, call the safety tool again with that detail immediately.
HANDOFF: transfer to ems when a clinician must talk the caller through care.
'''
