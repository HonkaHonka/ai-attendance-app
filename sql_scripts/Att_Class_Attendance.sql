
SET ANSI_NULLS ON
GO

SET QUOTED_IDENTIFIER ON
GO

CREATE TABLE [dbo].[Att_Class_Attendance](
	[iSerial] [int] NOT NULL,
	[StudentID] [varchar](50) NULL,
	[ClassNbr] [varchar](50) NULL,
	[ClassID] [varchar](50) NULL,
	[AttDateTime] [datetime] NULL,
	[AttDay] [varchar](50) NULL,
	[Created] [datetime] NULL,
	[CreatedBy] [varchar](50) NULL,
	[iTerm] [int] NULL,
	[Status] [varchar](50) NULL,
 CONSTRAINT [PK_Att_Class_Attendance] PRIMARY KEY CLUSTERED 
(
	[iSerial] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON, OPTIMIZE_FOR_SEQUENTIAL_KEY = OFF) ON [PRIMARY]
) ON [PRIMARY]
GO


