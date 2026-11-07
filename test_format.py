import unittest
import pandas as pd
from main import create_prompt, validate_evaluation, calculate_score

TRANSCRIPT = [
    {"speaker":"client", "timestamp":"00:00", "text":"Привет"},
    {"speaker":"operator", "timestamp":"00:01", "text":"Здравствуйте, меня зовут Анна."},
]

class FormatTests(unittest.TestCase):
    def sample(self):
        return {"conversation_id":"x", "criteria":[
            {"id":i,"result":({1:"YES",2:"N/A",3:"N/A",4:20,5:"YES",6:10,7:"N/A"}[i]),
             "explanation":"Проверено", "evidence_message_ids":["m2"] if i in (1,4,5,6) else []}
            for i in range(1,8)],
            "gross_errors":{"detected":False,"explanation":"","evidence_message_ids":[]}}
    def test_message_ids(self):
        p=create_prompt(pd.Series({"conversation_id":"x","channel":"chat","language":"ru"}),TRANSCRIPT)
        self.assertIn('[m2] [00:01] operator:',p)
    def test_valid(self):
        d=validate_evaluation(self.sample(),TRANSCRIPT)
        self.assertEqual(calculate_score(d['criteria']),100.0)
    def test_bad_evidence(self):
        d=self.sample();d['criteria'][0]['evidence_message_ids']=['m99']
        with self.assertRaises(ValueError): validate_evaluation(d,TRANSCRIPT)
    def test_bad_id(self):
        d=self.sample();d['criteria'][0]['id']='CONTACT'
        with self.assertRaises(ValueError): validate_evaluation(d,TRANSCRIPT)
    def test_invalid_na(self):
        d=self.sample();d['criteria'][0]['result']='N/A'
        with self.assertRaises(ValueError): validate_evaluation(d,TRANSCRIPT)
if __name__=='__main__':unittest.main()
