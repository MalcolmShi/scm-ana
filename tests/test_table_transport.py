"""本机表格文本正确处理逗号、换行、引号和空值。"""
import csv
import io
import unittest
import pandas as pd
from scripts.run_dashboard import table_text


class TableTransportTests(unittest.TestCase):
    def test_round_trip_values(self):
        frame=pd.DataFrame({'名称':['甲,乙','含"引号"\n第二行'],'数量':[1.25,None]})
        result=list(csv.reader(io.StringIO(table_text(frame).decode('utf-8'))))
        self.assertEqual(result,[['名称','数量'],['甲,乙','1.25'],['含"引号"\n第二行','']])
