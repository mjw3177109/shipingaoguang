"""V0.6 tests: research review-only path, temporal constraints and video matching."""
import json
import shutil
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .models import Candidate, Game, Venue
from .vision.bridge_v06 import (media_quick_fingerprint, propose_research_review)


HOOPS=[{"x":.40,"y":.18,"w":.1,"h":.1}]


def payload_for(game_id, duration, fingerprint, observations=None):
    if observations is None:
        observations=[
            {"time":2.0,"x":.43,"y":.23,"confidence":.82},
            {"time":2.6,"x":.445,"y":.21,"confidence":.86},
            {"time":3.2,"x":.464,"y":.18,"confidence":.90},
        ]
    return {
        "schema_version":2,"game_id":game_id,
        "video_duration_seconds":duration,
        "video_quick_fingerprint_sha256":fingerprint,
        "review_only":True,"research_only":True,
        "observations_truncated":False,"hoops":HOOPS,
        "analysis":{
            "device":"cpu","checkpoint_used":True,
            "model":"basketball_specialist_mobile320_v1",
            "incomplete_scan":False,"analyzed_until_seconds":duration,
        },
        "observations":observations,
    }


class ResearchBridgeRules(SimpleTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video=Path(self.tmp.name)/"fake-video.webm"
        self.video.write_bytes(b"V0.6 media bytes\0"+b"x"*100000)
        self.fp=media_quick_fingerprint(self.video)

    def get_clips(self, payload=None):
        return propose_research_review(payload or payload_for(12,10,self.fp),
            game_id=12,duration=10,hoops=HOOPS,video_fingerprint=self.fp)

    def test_three_moving_points_near_hoop_are_only_research_candidates(self):
        clips=self.get_clips()
        self.assertEqual(len(clips),1)
        self.assertEqual(clips[0]["source"],"model_proposal")
        self.assertIn("非投篮/命中结论",clips[0]["note"])
        self.assertEqual(clips[0]["start_seconds"],0)
        self.assertLess(clips[0]["end_seconds"],10.01)

    def test_no_hoop_track_or_motion_returns_zero(self):
        far=[
            {"time":2,"x":.8,"y":.7,"confidence":.9},
            {"time":2.5,"x":.83,"y":.73,"confidence":.8},
            {"time":3,"x":.85,"y":.72,"confidence":.8}]
        self.assertEqual(self.get_clips(payload_for(12,10,self.fp,far)),[])
        fixed=[{"time":i*.5+1,"x":.44,"y":.22,"confidence":.91}
               for i in range(4)]
        self.assertEqual(self.get_clips(payload_for(12,10,self.fp,fixed)),[])
        one=[{"time":1.5,"x":.44,"y":.20,"confidence":.91}]
        self.assertEqual(self.get_clips(payload_for(12,10,self.fp,one)),[])

    def test_wrong_game_roi_fingerprint_incomplete_and_truncated_fail_closed(self):
        for key,value in [
            ("game_id",13),
            ("video_quick_fingerprint_sha256","0"*64),
            ("hoops",[{"x":.1,"y":.1,"w":.1,"h":.1}]),
            ("observations_truncated",True),
            ("research_only",False),
            ("review_only",False),
            ("video_duration_seconds",20),
        ]:
            p=payload_for(12,10,self.fp)
            p[key]=value
            with self.subTest(field=key),self.assertRaises(ValueError):
                self.get_clips(p)
        for key,value in [("incomplete_scan",True),
                          ("analyzed_until_seconds",5),
                          ("checkpoint_used",False)]:
            p=payload_for(12,10,self.fp)
            p["analysis"][key]=value
            with self.subTest(field=key),self.assertRaises(ValueError):
                self.get_clips(p)

    def test_invalid_coordinates_and_nan_rejected(self):
        for key,value in [("x",float("nan")),("confidence",1.3),
                          ("time",-3),("y",True)]:
            p=payload_for(12,10,self.fp)
            p["observations"][1][key]=value
            with self.subTest(field=key),self.assertRaises(ValueError):
                self.get_clips(p)

    def test_fingerprint_detects_modified_file(self):
        old=self.fp
        self.video.write_bytes(b"different input"+"x".encode()*100000)
        self.assertNotEqual(media_quick_fingerprint(self.video),old)


class ResearchBridgeWebTests(TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        change=override_settings(MEDIA_ROOT=self.tmp.name)
        change.enable()
        self.addCleanup(change.disable)
        U=get_user_model()
        self.owner=U.objects.create_user(username="v06_owner",password="pass12345")
        self.other=U.objects.create_user(username="v06_other",password="pass12345")
        venue=Venue.objects.create(owner=self.owner,name="Private Court")
        self.game=Game.objects.create(
            venue=venue,title="Research Game",hoops=HOOPS,duration_seconds=10,
            video=SimpleUploadedFile("v06.webm",b"v06-real-upload-byte-identity"))
        self.fp=media_quick_fingerprint(self.game.video.path)
        self.url=reverse("import_research_observations",args=[self.game.pk])

    def send(self,payload):
        return self.client.post(self.url,{
            "observations":SimpleUploadedFile(
                "track.json",json.dumps(payload).encode(),content_type="application/json")
        })

    def test_owner_only_pending_dedupe_and_human_review(self):
        p=payload_for(self.game.pk,10,self.fp)
        self.client.force_login(self.other)
        self.assertEqual(self.send(p).status_code,404)
        self.client.force_login(self.owner)
        self.assertEqual(self.send(p).status_code,302)
        self.assertEqual(self.send(p).status_code,302)
        self.assertEqual(self.game.candidates.count(),1)
        clip=self.game.candidates.get()
        self.assertEqual(clip.review,"pending")
        self.assertEqual(clip.source,"model_proposal")
        self.assertFalse(self.game.exports.exists())
        self.client.post(reverse("queue_export",args=[self.game.pk]),{})
        self.assertFalse(self.game.exports.exists())
        response=self.client.post(reverse("review_candidate",args=[self.game.pk,clip.pk]),{
            "review":"approved","start":clip.start_seconds,
            "end":clip.end_seconds,"note":"人审只确认镜头，不代表进球"
        })
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.game.candidates.get().review,"approved")
        self.client.post(reverse("queue_export",args=[self.game.pk]),{})
        self.assertEqual(self.game.exports.count(),1)

    def test_import_errors_keep_existing_clips(self):
        self.client.force_login(self.owner)
        initial=Candidate.objects.create(
            game=self.game,start_seconds=.2,end_seconds=.8,
            source="manual",review="approved")
        p=payload_for(self.game.pk,10,self.fp)
        p["analysis"]["incomplete_scan"]=True
        self.assertEqual(self.send(p).status_code,302)
        self.assertEqual(self.game.candidates.count(),1)
        self.assertTrue(Candidate.objects.filter(pk=initial.pk,review="approved").exists())

    def test_zero_candidate_import_has_no_invented_clips(self):
        self.client.force_login(self.owner)
        p=payload_for(self.game.pk,10,self.fp,observations=[])
        self.assertEqual(self.send(p).status_code,302)
        self.assertFalse(self.game.candidates.exists())
